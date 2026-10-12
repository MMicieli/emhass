# EMHASS #1189 — Claude CLI independent final audit, code and test sweep

**Operator execution window:** Thursday **2026-10-15 Australia/Sydney**, only after the operator explicitly starts Claude CLI.  
**Canonical issue:** https://github.com/davidusb-geek/emhass/issues/1189  
**Existing analysis:** [issue_1189_dst_end_to_end_assurance_audit_2026-10-12.md](issue_1189_dst_end_to_end_assurance_audit_2026-10-12.md)  
**Status at handoff:** multi-path problem confirmed with isolated pandas evidence; no full-package/regression signoff and no upstream corrective PR represented as ready.

## Instructions to Claude CLI (copy or ask CLI to read this file and execute)

You are the **independent final technical auditor and implementation/test coordinator** for upstream EMHASS #1189. Work at gold-standard code correctness while respecting EMHASS upstream conventions and avoiding needless process, churn and duplicated tests. **Perform the work during this interactive CLI session**, not unattended or asynchronously. Provide actionable evidence, not unsupported pass/fail declarations.

### 0. Repository and safety preflight

1. Confirm `git rev-parse --show-toplevel`, `git remote -v`, `git status --short`, branch, HEAD and fork/upstream relationship. **DO NOT proceed on `MMicieli/home-assistant-config` accidentally:** code fixes belong in `MMicieli/emhass`. Do not reset/discard user changes, untracked worktrees or branches. If audit file is only in fork branch, read with `git show audit/1189-dst-path-assurance-20261012:docs/issue_1189_dst_end_to_end_assurance_audit_2026-10-12.md` or fetch the fork audit branch first. Keep work in new focused branches, not production/master.
2. Fetch and record the **current upstream master SHA** and compare it with the prior reviewed `224e1e715a2054bb9a1792d49847babc0f18a70f`. Revalidate every line-level claim against the new SHA; never assume previous refs or fork/master are equal. Review new upstream issue #1189 comments, related PRs and known DST fixes before coding.
3. Read **current upstream** `AGENTS.md`, `CONTRIBUTING.md`, `docs/develop.md`, `docs/develop_ai_coders.md`, `docs/passing_data.md`. Follow EMHASS's issue-first, scope, tests, code style, sign/unit and forecast input contracts; issue #1189 already exists.
4. **No live HA/EMS operations.** Do not connect to the operator's HA, call live EMHASS optimisation/actuation, restart add-ons, operate Sigenergy, write Modbus 40001 or protected 41501, change operator enable, or use production credentials/secrets. Use completely isolated synthetic fixtures and test environments. Any GitHub upstream PR/comment or release action requires the operator's explicit go-ahead after review.

### 1. Independent completeness sweep — call graph, not keyword-only review

Trace all relevant input producer → timestamp conversion/rounding → daily window creation → forecast transport/merge → optimiser → result persistence → publish / API paths, including error/restart/stale output. Inspect and give a disposition for all direct and indirect callers of:

- `utils.get_forecast_dates`, `add_local_calendar_days`, `_align_runtime_forecast_mapping`;
- `Forecast.__init__`, `get_forecast_days_csv`, `_extract_daily_forecast`, `get_forecast_out_from_csv_or_list`, `get_load_cost_forecast`, `get_prod_price_forecast`, `_get_load_forecast_naive`, and applicable list, CSV, mapping, PV/weather, typical and ML paths;
- `RetrieveHass._process_history_dataframe` **(new D13 mixed-offset REST-history reproducer)**, UTC-backed InfluxDB/VictoriaMetrics paths, WebSocket/REST timestamp parsing and resampling;
- `command_line.set_input_data_dict`, `prepare_forecast_and_weather_data`, `naive_mpc_optim`, `dayahead_forecast_optim`, `perfect_forecast_optim`;
- `optimization.Optimization.perform_optimization` and perfect-forecast day stepping;
- persistence `opt_res_latest.csv`, `_load_opt_res_latest`, `publish_data`, `publish_json`, `continual_publish`, `_get_closest_index`, `plan_store`, `last_run`, `/api/v1/plan`, `/api/v1/last-run`, API/web dispatch.

Include time operators (`floor/ceil/round`, `tz_localize/tz_convert`, `normalize`, `DateOffset/Timedelta`, `date_range`, `between_time`, `to_datetime`, `merge_asof`, `resample`, `read_json/read_csv`, wall-clock day grouping) and dependencies that could silently attach values to the wrong instant. Note any code paths not used by #1189 but related to DST separately. **Every suspected defect gets an exact repro or is explicitly classified as risk/unconfirmed; do not treat searches as proof.**

Preserve the current list-vs-timestamp-mapping contract: a plain list is positional with respect to the optimisation grid; a mapping is keyed by UTC instants. Freeze one request clock instant and preserve local-calendar-day semantics of #1164 and correct horizon sizing of #1166.


### 1a. New independent reporter evidence — obtain, verify, reuse (2026-10-12)

Read the **latest** comments on upstream [issue #1189](https://github.com/davidusb-geek/emhass/issues/1189), particularly [@thorstenhornung1's extensive independent confirmation and offered pytest suite](https://github.com/davidusb-geek/emhass/issues/1189#issuecomment-6117603789). The reporter independently corroborated our D1–D4 findings and reports **additional real-package failures D16–D23** now catalogued in the sister audit markdown (read that addendum in full).

**First priority: request/access the reporter's offered black-box tests if a GitHub branch or attachment is available.** Do not assume the suite was actually attached; as of our latest reading, only an offer exists. Ask for the branch/link through the operator if posting upstream is not authorised. Once available, review provenance and test safety; run unchanged against recorded upstream v0.18.5 and new master, establish xfails, then adopt/adapt focused tests with due credit and minimal duplication. The offered suite reportedly uses `time-machine` for deterministic clocks and covers full EMHASS functions without network or patched EMHASS. Do not add `time-machine` as a production runtime dependency.

**Must independently reproduce, not merely cite, the following additional modes:**

- **D16 first:** *two inconsistent grids even on normal days*. For Berlin 10:20 at 30-min `nearest`, show timestamped input's 10:00 price must **not** be used as 10:30, and test `last` mode. Prove `get_forecast_dates`, mapping alignment, `Forecast.__init__`, MPC start and #1166 window extension all use the same intended instant grid.
- **D17:** `Timestamp.replace(microsecond=0)` must not swap the actual fold/UTC instant; distinguish positive/negative DST and subsecond timestamp cases.
- **D18:** both `between_time` failure variants: omitted fold rows on MPC branch and overselected fold rows on perfect branch.
- **D19:** unbounded nearest cost CSV extrapolation for two hours; specify intended missing-data policy (explicit hold/interpolate/fail with required warnings) before fixing. No arbitrary change to other input contracts.
- **D20:** negative-DST zone `KeyError` from pytz/zoneinfo dtype conversions and `.loc` lookups under pandas 3; validate both supported pandas lines.
- **D21:** local ISO CSV with mixed offsets rejected by `pd.to_datetime(first_col,...)` preflight despite subsequent utc=True parse.
- **D22:** repeated UTC rounding on Open-Meteo path collapses a 30/60-min index in non-hour-offset `Asia/Kathmandu`.
- **D23:** `publish-data` must use persisted plan timestamps, not create a second incompatible grid; verify both-fold, failure and stale-plan paths.

**PR design change:** The earlier split into “rounding” and “forecast alignment” remains tentative; inspect whether a per-run canonical time grid is the minimal correct solution to D16 without unnecessary architectural refactoring. No isolated first PR should silently break normal-day prices while waiting for a subsequent PR. Preserve separate testable PRs **only where coherence and combined-head evidence justify them**, and ask maintainer for architecture agreement where appropriate.

### 2. Regression matrix — on actual EMHASS, not just pandas snippets

Use deterministically frozen clocks and complete real-package APIs/unit integration harness, not a calendar date on the real wall clock. Cover at least:

| Dimension | Required cases |
| --- | --- |
| Fall-back both folds | Europe/Berlin 2026-10-25: both 02:00 and 02:30 CEST/CET; Australia/Sydney 2027-04-04: both 02:00 and 02:30 AEDT/AEST, plus 5-minute 02:05/02:55 boundaries |
| Spring-forward nonexistent hour | Berlin 2026-03-29; Sydney 2026-10-04; before/during/after and windows that cross it |
| Unusual transitions | Australia/Lord_Howe 30-minute gap/fold; Europe/Dublin negative DST; Africa/Casablanca negative DST (Ramadan); Asia/Kathmandu non-hour offset |
| Clock edge cases | Just before, exactly at and just after optimisation boundaries; tie-to-nearest at half-step; host UTC vs configured site time; first/last/nearest modes |
| Native timestep | 5, 15, 30 and 60 minutes; no implicit rounding to 1 hour |
| Horizon | Requested exact counts, e.g. 1, 142, 287, 288 and calendar 23/24/25h or 23.5/24.5h as applicable |
| Forecast inputs | Positional list, timestamped mapping, CSV, load/PV/price, typical/naive, weather and any applicable predictor; value identity by absolute UTC instant |
| Outputs | Solver objective/input values and rows, unique monotonically increasing UTC timestamps, persistence CSV round-trip, full publish, `/api/v1/plan`, `/api/v1/last-run`, failed-run freshness and unchanged previous-run visibility |

**Assertions are mandatory:** expected absolute start equals chosen round/floor/ceil of same UTC instant; full sequence matches exact UTC grid at required frequency; one-to-one (instant,value) identity for distinguishable per-slot fixtures; first and last UTC timestamps correct; no duplicates, missing/fabricated slots, silent length changes, backward timestamps, hidden nearest-hour substitutions or cross-run result promotion. If expecting a deliberate fallback/interpolation, name and test its explicit contract; never assume `Optimal` or HTTP 200 proves semantic correctness.

Use existing tests whenever sufficient; add the smallest focused fixtures for uncovered gaps. Include positive no-regression baseline (normal dates, both hemispheres), and **failing reproductions against upstream master before fixing**, followed by passing post-patch tests on the exact proposed changes. In particular independently validate new D13 HA REST history mixed offsets, and do not overlook the D14 saved entity JSON UTC-`Z` round-trip that worked in isolation (avoid a false-positive patch there).

### 3. Implementation and PR design

If and only if reproduced in real EMHASS, implement minimum coherent fixes, normally in coordinated reviewable PR-sized slices:

1. **UTC-safe start rounding** in `get_forecast_dates` and `Forecast.__init__` (first/last/nearest), preserving configured local-calendar horizon semantics.
2. **Exact forecast transport and daily extraction** with strict UTC-instants/position contracts, fixing `between_time` repeated-fold selection, avoiding unbounded nearest masking and maintaining existing valid CSV/list/mapping behaviour.
3. **Combined integration regressions** if not already naturally included with slices 1 and 2. Don't open a test-only PR just to meet a count.

Treat D13 REST-history issue separately unless the maintainer and tests establish common ownership. Do not bundle unrelated #1190 (pandas 3 weather `fillna(method=...)`), #1167 fixed-AEST tariff semantics, or unrelated refactors into #1189 solely due to the DST keyword.

Every corrective PR should include a reproducer, its relevant regression(s), public-facing behaviour contract, links to #1189, compatibility effects, and concise documentation updates. Avoid duplicate tests, unused abstractions, renaming public columns/params or unnecessary dependencies. Prefer 2 focused PRs to 3 if the third is artificial. Prepare branches/diffs for review; **do not autonomously publish upstream PRs or merge**. Stacked/dependent PRs should be clearly labelled and tested on the combined head. GitHub merges sequentially, so eliminate unnecessary rebase work but never promise atomic merges.

### 3a. Independent operator-boundary check (offline only)

Read `MMicieli/home-assistant-config` at an explicitly recorded SHA **without changing that repository or touching Home Assistant**. Inspect `scripts/ems_production_runner.py`, `scripts/emhass_production_evidence.py::evaluate_binding`, `packages/18_ems_production_control_v1.yaml`, and their existing tests. The runner's plan/last-run binding is observational; Package18 handles failure return codes but does not independently verify every published value's t0 UTC identity. Use synthetic wrong-fold / publish-stale / false-HTTP-success negative cases to determine whether upstream E7 and site E10 actually protect physical dispatch. Do not introduce unrequested Package18 gates, operator state changes, node-red changes or Modbus writes. Distinguish published sensor freshness from forecast value-to-instant correctness. Record any risk and a bounded recommendation separately from the upstream corrective PR scope.

### 4. Canonical tests, environments and verification

Follow current upstream instructions rather than relying on these commands if they change:

```bash
uv sync --extra test
uv run pytest tests/
uvx ruff check .
uvx ruff format --check .
```

- Run targeted #1189 regressions first, then complete relevant and full suite on the exact proposed head. Record OS, Python, pandas, pytz, `uv`, CVXPY solver and EMHASS SHA, command, exit code, failure counts and test names.
- Validate at least pandas 2.2.3 and 3.0.6 where feasible using **separate isolated environments** without modifying the live add-on or overwriting operator setup. Clearly flag known unrelated upstream #1190 failures rather than papering over them or claiming an all-green suite.
- Where available, compare with official Linux/macOS/Windows CI; do not label another OS as tested locally when only CI ran there. Re-run after final rebase, conflict resolution or upstream change.
- No secret material in fixtures, logs, issue comments, commits or PR descriptions. Avoid giant generated artifacts or unbounded full log copies; retain concise repro commands and minimal relevant traces.


### Operator-mandated sequencing and narrow change boundary (2026-10-12)

**Apply strictly in this order:**

1. **Inventory only, no upgrades:** Record baseline resolved versions and upstream SHA from an isolated environment before edits. A read-only inventory may mention other packages, but those observations **do not authorise changes**.
2. **Correct the demonstrated DST defects first:** Reproduce and fix proven defects with the smallest relevant EMHASS source changes and targeted tests, then run end-to-end real-package regression tests, full applicable pytest and lint on the baseline dependency stack. Preserve UTC-instant/value identity and local-calendar-day semantics. Include adjacent code only where the call graph or regression evidence demonstrates it is affected. Do not automatically fix unrelated bugs merely because they involve timestamps, e.g. #1190.
3. **Only after a validated DST-fix baseline**, consider upgrades to packages **directly exercised by or technically necessary for the changed DST code** (for example, pandas and pytz/tzdata if actual compatibility/security evidence warrants). Upgrade only if genuinely safe, beneficial and supported; otherwise retain the current version. Test each prospective upgrade in a disposable environment against the *same* complete DST plus no-regression matrix. A version being newer is **not** a reason to upgrade.
4. **Exclude unrelated software from change scope:** Do not update NumPy, CVXPY, HiGHS, skforecast, Uvicorn, Gunicorn, aiohttp, HAOS, Node-RED, Sigenergy firmware, Docker base image, Python runtime or other packages solely to make them current. If an indirect dependency is *provably required* for a necessary directly affected upgrade, document the dependency chain and request operator approval before expanding the change scope. Otherwise record an optional, separately owned deferred finding.
5. **Separate deliverables:** Maintain individually and jointly tested DST PRs, with dependency changes in a separately reviewable follow-up PR only if a specific upgrade passes and is justified. Do not mix unrelated package maintenance into #1189. Any upstream PR submission/merges, live software update, HA/EMS/Modbus interaction or deployment requires separate explicit operator approval.

**Decision labels:** `DST_FIXED_AND_TESTED` or `DST_NOT_READY`; for each *directly affected* package `SAFE_UPGRADE_VERIFIED`, `KEEP_CURRENT`, `DEFER` or `NOT_TESTED`. Provide exact SHAs, resolved versions, tests and failure evidence. Never claim completion because isolated pandas primitives passed.

### 4a. Dependency, runtime and build reproducibility audit (additional operator request, 2026-10-12)

During the same interactive CLI sweep, perform a **read-only dependency inventory**, followed **only after the DST fix is validated** by isolated compatibility checks strictly limited to dependencies directly implicated by the DST repair. This is NOT authorisation to upgrade production or bundle dependency modernisation into #1189. The operator-mandated narrow change boundary above overrides any broader version-currency language below. Compare current upstream `pyproject.toml`, `.python-version`, `Dockerfile`, `.github/workflows/python-test.yml`, `.github/workflows/code-quality.yml`, `.github/dependabot.yml`, any generated lockfile or image metadata and the **actual resolved versions** in each tested environment. Pin all claims to an exact upstream SHA and date. Do not infer installed versions from lower bounds. Preserve release reproducibility and record the resolved dependency graph as evidence (without credentials).

Known candidate versions as of 2026-10-12 (REVERIFY AT EXECUTION; *not* upgrade targets by default): Python 3.15.0 versus supported `>=3.10,<3.13` and dev 3.12; pandas 3.0.6 versus `>=2.2.0`; pytz 2026.5 versus `>=2024.2`; NumPy 2.5.4 versus constrained `<2.3`; CVXPY 1.9.3 versus constrained `<1.8`; Uvicorn 0.54.0 versus exact `0.30.6`; skforecast 0.26.0 versus constrained `<0.23`. Verify latest actual versions and compatible transitive constraints from official package indexes. Versions outside an upper bound must not be assumed safe. Evaluate HiGHS/highspy, SciPy, pvlib, aiohttp, aioresponses, yarl and Python stdlib `asyncio` declared as a dependency when relevant. Note Uvicorn's deprecation of `uvicorn.workers.UvicornWorker` and separately assess the recommended `uvicorn-worker` integration. Check whether the Home Assistant Debian Bookworm base remains supported; do not automatically switch images.

**High-priority compatibility issue**: existing upstream [#1190](https://github.com/davidusb-geek/emhass/issues/1190) documents that pandas 3.0.6 removed `fillna(method=...)` while `command_line.prepare_forecast_and_weather_data` still uses it. A stale Open-Meteo cache after HTTP 503 can trigger a failing MPC followed by `publish-data` using a previous apparently `Optimal` plan. Do not confuse #1190 with #1189 or silently include it in DST PRs. Preserve an explicit failing-reproduction/owned-fix/test recommendation, including the all-NaN weather case where `.ffill().bfill()` alone is inadequate. Verify the presently declared `aiohttp<3.13` *test-only* cap due to aioresponses 0.7.8 and the `yarl<1.24` override before proposing change.

**Reproducibility concern to verify**: upstream does not currently track `uv.lock` in its default branch tree; the Dockerfile invokes `uv pip install .` then `uv lock`, while GitHub Actions runs `uv sync --reinstall --upgrade --extra test`. Determine whether independent builds can resolve materially different transitive versions and whether an opt-in reproducibility mechanism or dual dependency CI lane would help; do not force a lockfile governance change without maintainer agreement. Record upstream Dependabot weekly pip/docker configuration. A stable locked/reference lane plus deliberately upgraded compatibility lane is an option, not a mandate.

Deliver a **separate software-maintenance table for directly affected dependencies** (component, declared range, actual resolved version, current stable, evidence, compatibility tests, urgency, target owner/issue, upgrade recommendation, and no-change rationale); any other version gaps belong in read-only observations, not a proposed change list. Then explicitly decide `DST_PR_SCOPE` versus `SEPARATE_MAINTENANCE`. Only recommend upgrading a package where reproducible benefits outweigh regressions and the package remains in supported constraints. Never deploy upgrades to HAOS, the live EMHASS add-on, Node-RED, Sigenergy or any production EMS software without operator approval.

### 5. Final independent acceptance — deliver an audit decision, not just code

Return a concise table for **E1–E10** from the existing audit document, explicitly `PASS`, `FAIL`, `PARTIAL`, `NOT_RUN` with links or test/log evidence at each gate. Distinguish proven behaviour from hypothesis and unrelated issues. Summarise:

- Exact upstream/fork/base/feature/combined commit SHAs, any differences from prior inventory, all changed file paths and why, and duplicate/stale-test sweep.
- Source-to-output UTC/value invariants in actual packaged execution for both folds, multiple zones/timesteps, and both roundings/transport/publish.
- Actual commands and results, any failures with owner and severity, deliberately out-of-scope items and genuine risk on upcoming Europe 2026-10-25 / Australia 2027-04-04 transitions.
- PR split/dependencies and integrated head, CI readiness, reviewer requests, docs/AGENTS/LLM-friendly navigation amendments if needed.
- Final status: `READY_FOR_OPERATOR_PR_APPROVAL` or `NOT_READY` with reasons. **No artificial gates beyond material code/CI/safety risks**, but do not override a real alignment or publication defect to make checks green.

**At no point** should the Claude CLI represent a speculative proposal, source scan, mocked isolated primitive or an unexecuted command as a fully validated upstream fix. Stop to request operator approval before upstream PR creation, merges, live validation or production deployment. Never make an automatic Home Assistant control action.
