"""Fork-only pvlib/PyTables dependency and functional HDF5 audit.

This script is evidence gathering, not EMHASS runtime code.
"""
from __future__ import annotations

import importlib.metadata as metadata
import importlib.util
import json
import os
import re
import sys

import pandas as pd
import pvlib
from pvlib.clearsky import lookup_linke_turbidity


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


installed = {
    normalise(d.metadata.get("Name", "")): d.version
    for d in metadata.distributions()
    if d.metadata.get("Name")
}
pvlib_requirements = metadata.requires("pvlib") or []
pvlib_dependency_names = sorted(
    {normalise(m.group(1)) for item in pvlib_requirements
     if (m := re.match(r"\s*([a-zA-Z0-9_.-]+)", item))}
)
reverse_dependencies = {}
for package in metadata.distributions():
    name = normalise(package.metadata.get("Name", ""))
    for item in package.requires or []:
        match = re.match(r"\s*([a-zA-Z0-9_.-]+)", item)
        if match:
            dependent = normalise(match.group(1))
            if dependent in {"tables", "pytables", "numexpr", "h5py"}:
                reverse_dependencies.setdefault(dependent, []).append({
                    "required_by": name,
                    "raw_requirement": item,
                })

assert "tables" not in pvlib_dependency_names, "pvlib metadata directly requires tables"
assert "pytables" not in pvlib_dependency_names, "pvlib metadata directly requires pytables"
assert "numexpr" not in pvlib_dependency_names, "pvlib metadata directly requires numexpr"
assert "h5py" in pvlib_dependency_names, "Expected pvlib h5py requirement missing"
assert "h5py" in installed, "pvlib's HDF5 backend missing"
assert "tables" not in installed and "pytables" not in installed, "PyTables is installed"
assert importlib.util.find_spec("tables") is None, "PyTables importable unexpectedly"

times = pd.DatetimeIndex(
    ["2026-01-15 02:00:00", "2026-07-15 02:00:00"], tz="UTC"
)
# Linke turbidity is stored in pvlib's packaged LinkeTurbidities.h5,
# loaded via pvlib.clearsky using h5py (not PyTables).
turbidity = lookup_linke_turbidity(times, -35.25, 149.1)
assert len(turbidity) == len(times), f"Unexpected turbidity shape: {turbidity!r}"
assert bool(pd.notna(turbidity).all()), "Turbidity contains missing values"
assert all(0 < float(v) < 100 for v in turbidity), "Turbidity outside plausible bounds"

out = {
    "python": sys.version.split()[0],
    "platform": sys.platform,
    "pvlib": pvlib.__version__,
    "pvlib_declared_requirements": pvlib_requirements,
    "pvlib_dependency_names": pvlib_dependency_names,
    "installed_versions": {
        name: installed.get(name, "ABSENT")
        for name in [
            "pvlib", "h5py", "pandas", "tables", "pytables",
            "numexpr", "numpy", "scipy", "statsmodels", "numba"
        ]
    },
    "py_tables_importable": importlib.util.find_spec("tables") is not None,
    "numexpr_importable": importlib.util.find_spec("numexpr") is not None,
    "reverse_dependencies": reverse_dependencies,
    "linke_turbidity": [float(x) for x in turbidity],
    "package_count": len(installed),
    "run_label": os.environ.get("AUDIT_LABEL", ""),
}
print("DEPENDENCY_AUDIT_PASS|" + json.dumps(out, sort_keys=True), flush=True)
with open("pvlib-audit-results.json", "w", encoding="utf8") as f:
    json.dump(out, f, indent=2, sort_keys=True)
