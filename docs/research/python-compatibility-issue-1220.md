# EMHASS Python 3.11–3.15 compatibility research — upstream #1220

**Updated:** 2026-10-20. **Status:** research and qualification in progress, **not** a Python 3.14/3.15 support declaration.

**Owner:** this fork, for detailed compatibility research and reproducible evidence. [Upstream issue #1220](https://github.com/davidusb-geek/emhass/issues/1220) owns maintainer decisions and any approved upstream contribution. [Home Assistant compatibility watch #75](https://github.com/MMicieli/home-assistant-config/issues/75) tracks the upstream item at a high level; do **not** copy this investigation into #75.

This document is **fork-only research**, not upstream user documentation, a production deployment plan, or an extra upgrade approval gate. Do not include it in an upstream PR unless the operator explicitly authorises that scope.

## Target and agreed sequencing

Investigate **one EMHASS codebase that continues to work on Python 3.11–3.15**, with 3.14/3.15 prioritised as the newest stable pair. Retain 3.11–3.13 when practical; do not let unsupported assertions or arbitrary caps block newer interpreters. The maintainer [agreed to retain older compatible Python versions where feasible](https://github.com/davidusb-geek/emhass/issues/1220#issuecomment-6191735326) and [endorsed the incremental approach](https://github.com/davidusb-geek/emhass/issues/1220#issuecomment-6193660741).

1. **Stage 1 — 3.11–3.13:** remove Python 3.10 claims, resolve test isolation, and establish CI. [Fork implementation branch](https://github.com/MMicieli/emhass/tree/feat/python-311-313-issue-1220), initial commit `806da4ecb34266581014b7b867457b2dffb9e3c5`.
2. **Stage 2 — 3.14:** assess package compatibility and make only justified dependency/code changes.
3. **Stage 3 — 3.15:** assess wheel availability, native builds and all runtime/regression requirements; qualify when feasible.

The existing fork Stage 1 commit passed local static checks and, per the CLI handback dated **2026-10-20**, full Windows regression suites on Python 3.12 and 3.13; **Python 3.11 had one independently reproduced, pre-existing clock-sensitive test failure**, so the actual run is FAIL, not a clean pass. The report is from the operator's CLI; detailed logs were not included with the handback for independent inspection. **The exact Linux/macOS CI matrix remains unvalidated.** The author's [earlier upstream investigation](https://github.com/davidusb-geek/emhass/issues/1220#issuecomment-6192244830) reported a separate 3.12 baseline pass and 9,148 tests passing on 3.13; do not conflate that earlier baseline with this fork-commit run.

A package declaration such as `requires-python = ">=3.11"` expresses an installation floor; it is **not proof that every future Python minor release works**. Separate tested support from merely permitted installation.

## Dependency risk inventory

The constraints below were read from the fork's [Stage 1 `pyproject.toml`](https://github.com/MMicieli/emhass/blob/feat/python-311-313-issue-1220/pyproject.toml) and [Conda recipe](https://github.com/MMicieli/emhass/blob/feat/python-311-313-issue-1220/conda/meta.yaml) on 2026-10-20. Risk is an investigation priority, **not a confirmed failure**.

| Area | Current declaration / condition | Compatibility question and validation |
| --- | --- | --- |
| NumPy | `>=2.0,<2.3` | Existing upper bound may prevent newer Python builds; find the narrowest working, ABI-consistent NumPy range for each interpreter. |
| CVXPY | `>=1.6,<1.8` | Check newer CVXPY versions, native wheels, API, solver selection and objective/first-action equivalence; do not loosen blindly. |
| HiGHS and other CVXPY solvers | `highspy>=1.10`; CVXPY solver dependencies | Verify platform wheels or reproducible source builds, solver availability, feasibility/status, numeric tolerance, solve time and materially different schedules. |
| skforecast | `>=0.19.1,<0.23` | Check Python compatibility and whether a newer release requires forecasting API/model-artifact migration. Allowed 0.22.0 declares NumPy, pandas, SciPy, scikit-learn, Optuna and Numba dependencies; check the resolved transitive graph. |
| Numba / llvmlite | Transitive through skforecast | Verify exact Python/NumPy/LLVM support and amd64/ARM64 wheels for the selected resolved versions. Do not infer compatibility from one isolated latest release. |
| SciPy, pandas, scikit-learn | SciPy `>=1.15`, pandas `>=2.2`; scikit-learn transitive | Test native ABI compatibility with each chosen Python/NumPy combination, plus forecast/retraining equivalence. |
| h5py / PyTables / HDF5 | `h5py>=3.12.1`, `tables>=3.10`; Conda `pytables` | Verify wheels and native HDF5 libs; load and save representative persisted data/models; check source-build feasibility. |
| orjson and other compiled web dependencies | `orjson` unrestricted | Check wheel platform/glibc compatibility in the actual Debian base; a wheel published for Python 3.15 is not necessarily installable on Bookworm. |
| Web/application dependencies | `uvicorn==0.30.6`, `gunicorn>=23`, Quart, aiohttp, websockets, yarl | Test web-server startup, ASGI workers, API schema and normal request/response behavior; verify the exact old Uvicorn pin is still needed. |
| Test-only HTTP mocks | `aiohttp<3.13` in `test` extra | Repo documents `aioresponses` 0.7.8 mismatch with aiohttp 3.13+. Keep test limitation separate from production; only remove on evidence. |
| uv resolver override | `yarl<1.24` | Identify original failure and whether the override is still justified with resolved dependencies; do not replace it with another speculative pin. |
| Obsolete declaration candidate | External `asyncio` dependency | `asyncio` is in the Python standard library on the target interpreters. Check whether the standalone backport serves any actual purpose before removing it. |
| Python/build/Conda | `.python-version`, `uv`, Hatchling, Conda recipe | Keep declared package support, wheel/sdist metadata, actual installed interpreters and Conda constraints consistent; a single universal dependency lock is not assumed. |
| Home Assistant add-on | [Upstream Dockerfile](https://github.com/davidusb-geek/emhass/blob/master/Dockerfile) based on Home Assistant Debian Bookworm images | Check glibc, HDF5, amd64/ARM64, image availability, `uv python install`, `uv pip install`, `uv lock` and frozen startup; desktop success is not a production-image pass. |
| macOS/Windows/Linux CI | [Upstream test workflow](https://github.com/davidusb-geek/emhass/blob/master/.github/workflows/python-test.yml) | Check each selected interpreter actually executes; review macOS-specific old NumPy/PyTables bootstrap before trusting the matrix. |

**Version-specific dependency resolutions are acceptable** when necessary to retain Python 3.11 and also enable 3.15. Avoid an unnecessarily narrow global ceiling solely because a single old dependency release cannot run on all Python versions. Distinguish minimum supported EMHASS interpreter, supported release classifiers, tested CI matrix and actual deployable addon runtime.

## Qualification record — to populate from CLI results

Do **not** mark a result PASS without a reproducible command, exact Python version, resolved package versions and platform.

| Interpreter | Dependency resolution / install | Full regression suite | Binary / Docker platforms | Status |
| --- | --- | --- | --- | --- |
| Python 3.11.15 | CLI: resolved/installed on Windows | **FAIL:** 9,162 passed, 1 failed, 42 skipped, 102 xfailed, 1,897 subtests passed; identical upstream baseline failure reported | Linux/macOS CI and addon Docker not run | **Test qualification open** |
| Python 3.12.13 | CLI: resolved/installed on Windows | **PASS:** 9,163 passed, 42 skipped, 102 xfailed, 1,897 subtests passed | Linux/macOS CI and addon Docker not run | **Local Windows PASS** |
| Python 3.13.15 | CLI: resolved/installed on Windows | **PASS:** 9,163 passed, 42 skipped, 102 xfailed, 1,897 subtests passed | Linux/macOS CI and addon Docker not run | **Local Windows PASS** |
| Python 3.14 | Earlier upstream investigation identified newer NumPy/CVXPY and HTTP test-mock requirements | Not tested for this fork commit | Not verified | **OPEN** |
| Python 3.15 | Earlier upstream investigation reported missing required wheels; recheck current distributions and source-build options | Not tested for this fork commit | Not verified | **OPEN** |

### Stage 1 CLI handback — 2026-10-20 (operator-provided report)

**Source identity:** upstream baseline `86563131c93d618417c731ca0b09581a533e27a0`; unchanged fork implementation `806da4ecb34266581014b7b867457b2dffb9e3c5`. Isolated Windows worktrees/environment versions were reported, and all working trees were cleaned after testing. **No new code changes, upstream PRs or upstream comments were made.**

Reported `uv sync --upgrade --extra test` dependencies:

| Python | NumPy | pandas | CVXPY | SciPy | aiohttp | skforecast | PyTables | h5py |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3.11.15 | 2.2.6 | 3.0.6 | 1.7.5 | 1.17.1 | 3.12.15 | 0.19.1 | 3.11.1 | 3.16.0 |
| 3.12.13 | 2.2.6 | 3.0.6 | 1.7.5 | 1.18.1 | 3.12.15 | 0.19.1 | 3.11.1 | 3.16.0 |
| 3.13.15 | 2.2.6 | 3.0.6 | 1.7.5 | 1.18.1 | 3.12.15 | 0.19.1 | 3.11.1 | 3.16.0 |

**Confirmed by reported targeted reproduction, not yet independently inspected from logs:**

- The Python 3.11 `asyncio.Lock` cross-loop failure reappeared when the test fixture lock reset was temporarily removed, and did **not** recur with the reset. The test-case loop isolation is the expected cause; no production loop-swap evidence was identified.
- `tests/test_optimization.py::TestOptimization::test_zone_overshoot_bounds_the_last_step_with_the_zone_loss` failed on 3.11 **and** 3.12 and on unmodified upstream, but passed at other clock times. The CLI reported failure around **11:39 UTC**, pass at **00:00, 06:00, 12:00, 18:00, 23:00 UTC** on the tested day, and failure at **12:00 UTC on 2026-10-24**. DST involvement was suggested but **not demonstrated**. Do not attribute this failure to Stage 1 or Python 3.11 without contrary evidence.
- Targeted remediation: pin the test's wall clock deterministically using existing `time-machine` test dependency, with representative boundary cases as needed; do **not** change thermal optimisation mathematics or silently loosen the assertion. Perform as a narrow, separately reviewed test-fix change rather than folding unrelated behavioural changes into #1220.
- Ruff check, Ruff format, `uv build`, wheel/sdist `Requires-Python >=3.11` and classifiers: **CLI-reported PASS**. Conda build, Docker build and docs build: **NOT RUN**.
- GitHub Actions: **zero recorded workflow runs** for commit `806da4e` (also independently confirmed by GitHub API on 2026-10-20). CLI reported workflow dispatch returned **HTTP 422** because Actions are disabled on the personal fork. Enabling Actions requires an owner decision; alternatively obtain separately authorised upstream PR CI or another genuine cross-platform runner. Local Windows tests do not validate macOS/Linux.

**Assessment:** The Stage 1 patch appears technically sound, but the local 3.11 full-suite run is **FAIL** and the cross-platform CI qualification is **NOT RUN**. Treat the CLI's `GOLD_STANDARD_REVIEW = PASS` as a *code-review verdict only*, not a completed support certification. No new Python maximum-version cap, package pin, compatibility layer or extra approval gate is warranted from these findings. The separate obsolete external `asyncio` package entry may be assessed as bounded future hygiene, **not** silently bundled into Stage 1.

For each interpreter: record `python --version`, OS/architecture, resolver output, actual resolved distribution versions, binary-wheel vs source-build provenance, full pytest results, Ruff/build checks and limitations. Preserve exact failing commands and error signatures without committing credentials, tokens or private site data.

**Functional baseline:** verify day-ahead, naive MPC, perfect-forecast, forecasting and persisted-data handling, DST, publishing and API behaviour. Beyond `optim_status=Optimal`, compare objective, solver status, battery/grid first action, SOC evolution and forecasts under identical input fixtures. Record tolerances and measured solve-time / five-minute control-budget effects; **changed feasible schedules are not automatically equivalent**. Prefer independent off-box fixtures over writes to live HA/EMHASS.

## Decision rules

- Correct, reproducible compatibility evidence precedes dependency changes. Keep legitimate bounds until a tested alternative is demonstrated.
- Prefer minimal native dependency/runtime updates and version markers when necessary; **no custom package allowlists, speculative max-Python cap, duplicated compatibility framework or extra approval gates**.
- Do not change EMHASS optimisation mathematics or forecast semantics merely to make installation pass.
- Keep the upstream contribution incremental, small and reviewable. **No upstream PR, issue/discussion comment, merge or live deployment without explicit operator approval.**
- Update this note with *material findings* and dated evidence, not every experiment or routine check. Keep #75 to a watch-level pointer, and do not turn it into a duplicate research log.
