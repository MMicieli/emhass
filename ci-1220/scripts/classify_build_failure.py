"""Classify a failed source build as a missing prerequisite or a genuine compile problem.

    python classify_build_failure.py LOG [LOG ...] [--json out.json]

Categories
  MISSING_PREREQUISITE  the toolchain/library was not found; the code was never compiled
  COMPILE_ERROR         the compiler or Cython ran and rejected the source (candidate Python 3.15 incompatibility)
  LINK_ERROR            compiled, then the linker could not resolve a library (usually a prerequisite path issue)
  OTHER                 unclassified; a human must read the log

This is a heuristic over text. It never decides on its own that a package is "incompatible with 3.15":
COMPILE_ERROR lines are printed verbatim so a person can judge whether they mention removed or changed
CPython APIs (the `py315_hint` flag only marks lines that name a CPython C-API symbol or `Py_`/`_Py` macro).
The raw logs are always kept as the authoritative evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MISSING = [
    r"Microsoft Visual C\+\+ 14\.0 or greater is required",
    r"CMAKE_(C|CXX|Fortran)_COMPILER not set",
    r"No CMAKE_(C|CXX)_COMPILER could be found",
    r"CMake was unable to find a build program",
    r"Unable to load dependency HDF5",
    r"Could NOT find (HDF5|BLAS|LAPACK|OpenBLAS|ZLIB)",
    r"Could not find (a package configuration file|HDF5|BLAS)",
    r"cannot open include file: '(hdf5|zlib|cblas|lapacke)\.h'",
    r"fatal error C1083: Cannot open include file",
    r"'(nmake|ninja|cmake)' (is not recognized|not found)",
    r"No module named '(Cython|numpy|pkgconfig|setuptools|scikit_build_core)'",
    r"\bvcvarsall\.bat\b.*not found",
]
LINK = [r"LNK1181", r"LNK2019", r"LNK2001", r"LNK1104", r"cannot open input file"]
COMPILE = [
    r"error C\d{4}:",
    r"\.(c|cc|cpp|cxx|h|hpp|pyx|pxd):\d+(:\d+)?: error",
    r"Error compiling Cython file",
    r"undeclared identifier",
    r"has no member named",
    r"is not a member of",
    r"too few arguments to function",
    r"incompatible (pointer )?type",
    r"unresolved external symbol.*Py",
]
PY_API = re.compile(r"\b(_?Py[A-Z_a-z]+|PyObject|PyLongObject|PyFrameObject|PyCode|Py_[A-Za-z_]+|ob_digit|tp_[a-z_]+|_PyLong\w*)\b")


def classify(text: str) -> dict:
    hits = {"MISSING_PREREQUISITE": [], "LINK_ERROR": [], "COMPILE_ERROR": []}
    for line in text.splitlines():
        if any(re.search(p, line) for p in MISSING):
            hits["MISSING_PREREQUISITE"].append(line.strip())
        elif any(re.search(p, line) for p in LINK):
            hits["LINK_ERROR"].append(line.strip())
        elif any(re.search(p, line) for p in COMPILE):
            hits["COMPILE_ERROR"].append(line.strip())
    # a genuine compile error outranks a missing prerequisite only if code was actually compiled
    if hits["COMPILE_ERROR"]:
        verdict = "COMPILE_ERROR"
    elif hits["LINK_ERROR"]:
        verdict = "LINK_ERROR"
    elif hits["MISSING_PREREQUISITE"]:
        verdict = "MISSING_PREREQUISITE"
    else:
        verdict = "OTHER" if re.search(r"error|Error|failed", text) else "OK"
    evidence = hits.get(verdict, [])[:6]
    return {
        "verdict": verdict,
        "evidence": evidence,
        "py315_hint": verdict == "COMPILE_ERROR" and any(PY_API.search(e) for e in evidence),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    out = {}
    for p in a.logs:
        path = Path(p)
        out[path.stem] = classify(path.read_text(encoding="utf-8", errors="replace"))
    for name, r in out.items():
        flag = "  [mentions CPython C-API: review for 3.15 incompatibility]" if r["py315_hint"] else ""
        print(f"{name:<14} {r['verdict']:<21}{flag}")
        for e in r["evidence"][:2]:
            print(f"    {e[:140]}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
