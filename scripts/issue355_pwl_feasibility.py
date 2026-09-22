#!/usr/bin/env python3
"""Issue #355 E3 research-only PWL feasibility benchmark.

This file is deliberately isolated from production EMHASS paths. It does NOT
modify Optimization, configuration, publishing or hardware behaviour.

Purpose:
1. Freeze the accepted Gate A/B M4 coefficients.
2. Derive shortest chordal PWL representations from that SAME curve (no refit).
3. Quantify approximation error and exact incremental-PWL MILP burden at the
   site's 288-step horizon.
4. Optionally exercise the formulation with CVXPY/HiGHS when --solve is used.

The production candidate, if E3 advances, is one decision-dependent PWL:
    x = P_PV + P_discharge
    loss = L_pwl(x) - L(P_PV) + night_mask * L(0) * E
where P_PV and night_mask are known parameters and E is the existing EMHASS
battery direction binary. This makes PV-only and charging exact no-ops.
Simultaneous PV curtailment + battery discharge remains outside validated #355
physics and is intentionally NOT solved here by adding a second PWL.
"""

from __future__ import annotations

import argparse
import json
import math
import time

import numpy as np

A = 0.15919898866861668
S = 0.0760571105679393
B = 0.005013869243073287
C = 0.002758702385360409
K = 0.5

DOMAIN_MAX_KW = 15.0
SEARCH_STEP_KW = 0.05
VALIDATION_STEP_KW = 0.001
DEFAULT_HORIZON = 288
TOLERANCES_W = (5, 10, 15, 20, 25, 30, 40)


def loss_kw(x):
    x = np.asarray(x, dtype=float)
    return A + S * (1.0 - np.exp(-x / K)) + B * x + C * x * x


def _edge_error_w(x0, x1, y0, y1, dense_x, dense_y):
    mask = (dense_x >= x0 - 1e-12) & (dense_x <= x1 + 1e-12)
    pred = np.interp(dense_x[mask], [x0, x1], [y0, y1])
    err_w = np.abs((pred - dense_y[mask]) * 1000.0)
    return float(np.max(err_w))


def shortest_representation(max_error_w):
    grid_x = np.arange(0.0, DOMAIN_MAX_KW + SEARCH_STEP_KW / 2, SEARCH_STEP_KW)
    grid_y = loss_kw(grid_x)
    dense_x = np.arange(
        0.0, DOMAIN_MAX_KW + VALIDATION_STEP_KW / 2, VALIDATION_STEP_KW
    )
    dense_y = loss_kw(dense_x)

    n = len(grid_x)
    inf = n + 1
    dist = [inf] * n
    prev = [None] * n
    dist[0] = 0

    edge_cache = {}

    for i in range(n - 1):
        if dist[i] == inf:
            continue
        for j in range(i + 1, n):
            key = (i, j)
            err = edge_cache.get(key)
            if err is None:
                err = _edge_error_w(
                    grid_x[i], grid_x[j], grid_y[i], grid_y[j], dense_x, dense_y
                )
                edge_cache[key] = err
            if err <= max_error_w + 1e-12 and dist[i] + 1 < dist[j]:
                dist[j] = dist[i] + 1
                prev[j] = i

    if dist[-1] == inf:
        raise RuntimeError(f"no representation found for {max_error_w} W")

    indices = []
    cur = n - 1
    while cur is not None:
        indices.append(cur)
        cur = prev[cur]
    indices.reverse()

    bp_x = grid_x[indices]
    bp_y = grid_y[indices]

    dense_pred = np.interp(dense_x, bp_x, bp_y)
    signed_error_w = (dense_pred - dense_y) * 1000.0

    return {
        "requested_max_error_w": max_error_w,
        "segments": len(bp_x) - 1,
        "breakpoints_kw": [float(x) for x in bp_x],
        "loss_w": [float(y * 1000.0) for y in bp_y],
        "validated_max_abs_error_w": float(np.max(np.abs(signed_error_w))),
        "validated_rmse_w": float(np.sqrt(np.mean(signed_error_w**2))),
    }


def burden(rep, horizon):
    # Exact incremental PWL graph:
    #   one non-negative delta variable per segment/timestep
    #   one binary per segment transition/timestep
    #   x == sum(delta)
    #   delta_i >= width_i*z_i
    #   delta_{i+1} <= width_{i+1}*z_i
    m = rep["segments"]
    return {
        "horizon": horizon,
        "continuous_delta_variables": m * horizon,
        "new_binary_variables": max(0, m - 1) * horizon,
        "scalar_segment_upper_bounds": m * horizon,
        "scalar_ordering_constraints": 2 * max(0, m - 1) * horizon,
        "scalar_x_equalities": horizon,
    }


def optional_solve(rep, horizon):
    try:
        import cvxpy as cp
    except ImportError:
        return {"status": "SKIPPED", "reason": "cvxpy not installed"}

    if "HIGHS" not in cp.installed_solvers():
        return {
            "status": "SKIPPED",
            "reason": f"HiGHS not installed; available={cp.installed_solvers()}",
        }

    bp_x = np.asarray(rep["breakpoints_kw"], dtype=float)
    bp_y = np.asarray(rep["loss_w"], dtype=float) / 1000.0
    widths = np.diff(bp_x)
    slopes = np.diff(bp_y) / widths
    m = len(widths)

    # Deterministic site-sized discharge-allocation proxy.  It is intentionally
    # not presented as an EMHASS replay; it only exercises the added PWL MILP
    # structure under an energy budget and varying AC demand ceilings.
    p = cp.Variable(horizon, nonneg=True, name="issue355_p")
    delta = cp.Variable((m, horizon), nonneg=True, name="issue355_delta")
    z = (
        cp.Variable((m - 1, horizon), boolean=True, name="issue355_seg")
        if m > 1
        else None
    )

    constraints = [p <= DOMAIN_MAX_KW, p == cp.sum(delta, axis=0)]
    for i in range(m):
        constraints.append(delta[i] <= widths[i])
    if z is not None:
        for i in range(m - 1):
            constraints.append(delta[i] >= widths[i] * z[i])
            constraints.append(delta[i + 1] <= widths[i + 1] * z[i])

    loss = bp_y[0] + cp.sum(cp.multiply(slopes[:, None], delta), axis=0)
    delivered = p - loss

    phase = np.arange(horizon, dtype=float)
    demand = 0.4 + 13.6 * (0.5 + 0.5 * np.sin(2 * np.pi * phase / 73.0))
    price = 0.08 + 0.24 * (0.5 + 0.5 * np.sin(2 * np.pi * (phase - 17) / 97.0))
    constraints += [
        delivered <= demand,
        cp.sum(p) * (5.0 / 60.0) <= 24.0,
    ]

    problem = cp.Problem(cp.Maximize(cp.sum(cp.multiply(price, delivered))), constraints)

    t0 = time.perf_counter()
    value = problem.solve(solver=cp.HIGHS, verbose=False)
    elapsed = time.perf_counter() - t0

    return {
        "status": str(problem.status),
        "objective": None if value is None else float(value),
        "solve_seconds": elapsed,
        "solver": "HIGHS",
        "num_scalar_variables": int(problem.size_metrics.num_scalar_variables),
        "num_scalar_eq_constr": int(problem.size_metrics.num_scalar_eq_constr),
        "num_scalar_leq_constr": int(problem.size_metrics.num_scalar_leq_constr),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--solve", action="store_true")
    args = parser.parse_args()

    report = {
        "issue": 355,
        "candidate": "frozen Gate A/B M4; chordal PWL only; no refit",
        "domain_kw": [0.0, DOMAIN_MAX_KW],
        "search_step_kw": SEARCH_STEP_KW,
        "validation_step_kw": VALIDATION_STEP_KW,
        "horizon": args.horizon,
        "representations": [],
    }

    for tol in TOLERANCES_W:
        rep = shortest_representation(tol)
        rep["burden"] = burden(rep, args.horizon)
        if args.solve:
            rep["microbenchmark"] = optional_solve(rep, args.horizon)
        report["representations"].append(rep)

    print("ISSUE355_E3_PWL_FEASIBILITY")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("PRODUCTION_CHANGE=NONE")


if __name__ == "__main__":
    main()
