"""THROWAWAY (fork-only, not part of the feature PR): default-OFF parity vs upstream master.

Runs tests/_parity/parity_dump.py with the same interpreter and dependencies twice - once
against this checkout's src/ (curves absent/default) and once against master's src/ fetched
from the fork at the base SHA - and requires byte-identical output.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile

BASE_SHA = "237d0fe4089f76ff5f8bbcae24876e64008e60ad"
FORK = "https://github.com/MMicieli/emhass.git"
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "_parity" / "parity_dump.py"


def _run(cwd: pathlib.Path) -> str:
    env = {**os.environ, "PYTHONPATH": str(cwd / "src")}
    done = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr[-3000:]
    return done.stdout


def test_default_off_parity_exact():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp) / "base"
        base.mkdir()
        for cmd in (
            ["git", "init", "-q"],
            ["git", "remote", "add", "origin", FORK],
            ["git", "fetch", "-q", "--depth", "1", "origin", BASE_SHA],
            ["git", "checkout", "-q", "FETCH_HEAD"],
        ):
            subprocess.run(cmd, cwd=base, check=True)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=base, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert head == BASE_SHA
        master_out = _run(base)
        branch_out = _run(ROOT)
    master, branch = json.loads(master_out), json.loads(branch_out)
    print("PARITY_CONFIGS", sorted(master))
    assert sorted(master) == sorted(branch)
    for name in master:
        print(name, master[name]["status"], master[name]["n_variables"], master[name]["n_constraints"])
    assert master_out == branch_out, "DEFAULT_OFF_PARITY differs"
    print("DEFAULT_OFF_PARITY=EXACT")
