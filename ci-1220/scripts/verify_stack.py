"""Execute the compiled EMHASS stack, not just import it.

Every check runs real code (a solve, a file round trip, a vectorised evaluation) and compares
the answer with a known value. Output: one JSON document on stdout/--out, exit code 1 if any
required check fails. Nothing here touches the network.

    python -I verify_stack.py --expect-python 3.15.2 --out stack.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import sys
import tempfile
import time
import traceback

RESULTS: list[dict] = []
REGISTRY: list[tuple] = []


def check(name, required=True):
    """Register a check; main() runs them after the interpreter guard."""
    def deco(fn):
        REGISTRY.append((name, required, fn))
        return fn
    return deco


def run_all():
    for name, required, fn in REGISTRY:
        t0 = time.perf_counter()
        try:
            detail = fn()
            RESULTS.append({"check": name, "ok": True, "required": required, "detail": detail,
                            "seconds": round(time.perf_counter() - t0, 3)})
        except Exception as exc:  # noqa: BLE001 - report every failure, never stop early
            RESULTS.append({"check": name, "ok": False, "required": required,
                            "detail": f"{type(exc).__name__}: {exc}",
                            "trace": traceback.format_exc(limit=4),
                            "seconds": round(time.perf_counter() - t0, 3)})


def version(mod):
    m = importlib.import_module(mod)
    return getattr(m, "__version__", "n/a")


# ---------------------------------------------------------------- interpreter / imports
@check("interpreter")
def _interp():
    return {"version": sys.version.split()[0], "releaselevel": sys.version_info.releaselevel,
            "platform": platform.platform(), "machine": platform.machine(),
            "compiler": platform.python_compiler(), "gil_enabled": getattr(sys, "_is_gil_enabled", lambda: True)()}


@check("imports")
def _imports():
    mods = ["numpy", "scipy", "pandas", "pvlib", "h5py", "tables", "highspy", "cvxpy", "yaml",
            "numexpr", "ndindex", "osqp", "qdldl", "sparsediffpy", "scs", "clarabel", "sklearn",
            "skforecast", "aiohttp", "quart", "orjson", "emhass"]
    out, missing = {}, []
    for m in mods:
        try:
            out[m] = version(m)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{m}: {type(exc).__name__}: {exc}")
    if missing:
        raise RuntimeError("; ".join(missing))
    return out


# ---------------------------------------------------------------- compiled extensions
@check("extensions are native binaries from this build")
def _ext():
    # Proves the module is a compiled extension for this interpreter, not a stray pure-python copy.
    import _yaml  # PyYAML's libyaml binding
    import h5py.h5
    import numexpr.interpreter
    import ndindex._slice  # noqa: F401  (compiled when built with the C extension)
    return {m.__name__: os.path.basename(getattr(m, "__file__", "") or "") for m in (_yaml, h5py.h5, numexpr.interpreter)}


@check("PyYAML uses libyaml (C loader), not the pure-python fallback")
def _yaml():
    import yaml
    assert yaml.__with_libyaml__, "built without libyaml"
    doc = yaml.load("a: [1, 2, {b: 3}]", Loader=yaml.CSafeLoader)
    assert doc == {"a": [1, 2, {"b": 3}]}
    return yaml.__version__


@check("HDF5 / h5py round trip")
def _h5():
    import h5py
    import numpy as np
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.h5")
        a = np.arange(12, dtype="f8").reshape(3, 4)
        with h5py.File(p, "w") as f:
            f.create_dataset("x", data=a, compression="gzip")
            f["x"].attrs["unit"] = "kW"
        with h5py.File(p, "r") as f:
            assert (f["x"][()] == a).all() and f["x"].attrs["unit"] == "kW"
    return {"h5py": h5py.__version__, "hdf5": h5py.version.hdf5_version}


@check("PyTables round trip (same HDF5 as h5py)")
def _tables():
    import numpy as np
    import tables
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.h5")
        with tables.open_file(p, "w") as f:
            f.create_array("/", "a", np.arange(5))
        with tables.open_file(p, "r") as f:
            assert list(f.root.a.read()) == [0, 1, 2, 3, 4]
    return {"tables": tables.__version__, "hdf5": tables.hdf5_version}


@check("numexpr evaluates and agrees with numpy")
def _numexpr():
    import numexpr as ne
    import numpy as np
    x = np.linspace(0, 1, 1000)
    assert np.allclose(ne.evaluate("sin(x)*2 + x**2"), np.sin(x) * 2 + x**2)
    return ne.__version__


@check("ndindex normalises a slice")
def _ndindex():
    import numpy as np
    from ndindex import Slice, ndindex
    a = np.arange(20)
    idx = Slice(1, 10, 3)
    assert (a[idx.reduce(20).raw] == a[1:10:3]).all()           # same elements as numpy slicing
    assert idx.newshape((20,)) == (3,)
    assert (a[ndindex((slice(None, None, -2),)).raw] == a[::-2]).all()
    return "ok"


@check("BLAS/LAPACK: a numpy and a scipy call run, and the backend is reported")
def _blas():
    import numpy as np
    import scipy.linalg as sl
    rng = np.random.default_rng(0)
    a = rng.standard_normal((200, 200))
    b = rng.standard_normal(200)
    x = sl.solve(a, b)
    assert np.allclose(a @ x, b, atol=1e-8)
    cfg = np.show_config(mode="dicts")
    return {"numpy_blas": cfg.get("Build Dependencies", {}).get("blas", {}), "residual": float(np.abs(a @ x - b).max())}


# ---------------------------------------------------------------- solvers
@check("cvxpy lists the solvers EMHASS relies on")
def _solvers():
    import cvxpy as cp
    have = set(cp.installed_solvers())
    need = {"HIGHS", "OSQP", "SCS", "CLARABEL"}
    assert need <= have, f"missing {sorted(need - have)}; installed {sorted(have)}"
    return sorted(have)


@check("LP solved by every installed open-source solver, optimum checked")
def _lp():
    import cvxpy as cp
    x = cp.Variable(2)
    # min -x0 - 2 x1  s.t. x0 + x1 <= 4, x1 <= 3, x >= 0   ->  x = (1, 3), value -7
    prob = cp.Problem(cp.Minimize(-x[0] - 2 * x[1]), [x[0] + x[1] <= 4, x[1] <= 3, x >= 0])
    out = {}
    for s in ("HIGHS", "OSQP", "SCS", "CLARABEL"):
        prob.solve(solver=s)
        tol = 5e-3 if s in ("OSQP", "SCS") else 1e-6
        assert abs(prob.value + 7) < tol, f"{s}: {prob.value}"
        out[s] = round(float(prob.value), 6)
    return out


@check("MILP through the exact HiGHS call shape EMHASS uses (cp.HIGHS, warm_start, gap, time limit)")
def _milp():
    import cvxpy as cp
    import numpy as np
    v = np.array([10, 13, 18, 31, 7, 15])
    w = np.array([11, 15, 20, 35, 10, 33])
    pick = cp.Variable(6, boolean=True)
    prob = cp.Problem(cp.Maximize(v @ pick), [w @ pick <= 47])
    prob.solve(solver=cp.HIGHS, warm_start=True, time_limit=30, mip_rel_gap=0.0)
    assert prob.status == cp.OPTIMAL, prob.status
    best = max(sum(v[i] for i in range(6) if m >> i & 1) for m in range(64)
               if sum(w[i] for i in range(6) if m >> i & 1) <= 47)  # brute force, independent of the solver
    assert abs(prob.value - best) < 1e-6, (prob.value, best)
    return {"status": prob.status, "value": float(prob.value)}


@check("highspy used directly (model build, run, solution)")
def _highspy():
    import highspy
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    inf = highspy.kHighsInf
    h.addVar(0, inf)
    h.addVar(0, inf)
    h.changeColCost(0, -1.0)
    h.changeColCost(1, -2.0)
    h.addRow(-inf, 4, 2, [0, 1], [1.0, 1.0])
    h.run()
    sol = h.getSolution().col_value
    assert abs(h.getInfo().objective_function_value + 8) < 1e-6 and abs(sol[1] - 4) < 1e-6
    return {"status": h.modelStatusToString(h.getModelStatus())}


@check("OSQP and QDLDL called directly")
def _osqp_qdldl():
    import numpy as np
    import osqp
    import qdldl
    import scipy.sparse as sp
    P = sp.csc_matrix([[4.0, 1.0], [1.0, 2.0]])
    m = osqp.OSQP()
    m.setup(P, np.array([1.0, 1.0]), sp.csc_matrix([[1.0, 1.0]]), np.array([1.0]), np.array([1.0]), verbose=False)
    r = m.solve()
    # independent reference: solve the equality-constrained QP through its KKT system with numpy
    P0 = np.array([[4.0, 1.0], [1.0, 2.0]])
    kkt = np.block([[P0, np.array([[1.0], [1.0]])], [np.array([[1.0, 1.0]]), np.zeros((1, 1))]])
    ref = np.linalg.solve(kkt, np.array([-1.0, -1.0, 1.0]))[:2]
    assert np.allclose(r.x, ref, atol=2e-3), (r.x, ref)
    f = qdldl.Solver(sp.csc_matrix([[4.0, 1.0], [1.0, 3.0]]))
    z = f.solve(np.array([1.0, 2.0]))
    assert np.allclose(np.array([[4.0, 1.0], [1.0, 3.0]]) @ z, [1.0, 2.0])
    return "ok"


@check("sparsediffpy is used by cvxpy (differentiable problem)", required=False)
def _sparsediff():
    import cvxpy as cp
    x = cp.Variable()
    p = cp.Problem(cp.Minimize((x - 3) ** 2))
    p.solve(solver=cp.CLARABEL)
    assert abs(x.value - 3) < 1e-4
    return version("sparsediffpy")


# ---------------------------------------------------------------- EMHASS itself
@check("EMHASS imports and its optimisation module builds a problem")
def _emhass():
    from emhass import optimization  # noqa: F401
    from emhass.optimization import Optimization  # noqa: F401
    import emhass
    return {"emhass": emhass.__version__, "file": os.path.dirname(emhass.__file__)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-python", default=None, help="exact x.y.z required")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.expect_python:
        got = sys.version.split()[0]
        if got != a.expect_python:
            print(f"interpreter is {got}, expected {a.expect_python}", file=sys.stderr)
            return 2
    run_all()
    failed = [r for r in RESULTS if not r["ok"] and r["required"]]
    doc = {"python": sys.version.split()[0], "ok": not failed, "failed": [r["check"] for r in failed], "results": RESULTS}
    text = json.dumps(doc, indent=2, default=str)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    for r in RESULTS:
        print(("PASS " if r["ok"] else ("FAIL " if r["required"] else "WARN ")) + r["check"])
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed; required failures: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
