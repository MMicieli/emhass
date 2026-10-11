# EMHASS #1189 — DST upstream/downstream assurance inventory (audit-in-progress)

**Date:** 2026-10-12 (Australia/Sydney)  
**Upstream issue:** https://github.com/davidusb-geek/emhass/issues/1189  
**Reviewed upstream master tree:** `224e1e715a2054bb9a1792d49847babc0f18a70f`  
**Fork master tree at audit:** `6cbb4a0128fa38645ef70681a95e60944d49d337` (divergent; do not assume fork/upstream code parity)  
**Disposition:** **CONFIRMED MULTI-PATH DEFECT / END-TO-END RELEASE ASSURANCE OPEN**. This record is not a fix, PR, test-suite completion, deployment authorisation or declaration that all DST bugs are resolved.

## Decision and scope

Fixing only `utils.get_forecast_dates().floor()` does not close #1189. Audit producers, transforms, forecast inputs, optimiser, persisted plans and publication using **absolute UTC instant** identity, while maintaining explicit local-calendar-day semantics. One existing upstream issue is the authoritative issue tracker; do not create duplicate local implementation backlogs.

Current evidence consists of source inspection of upstream's default branch and **isolated** reproducible pandas checks in pandas **2.2.3** / pytz **2026.2**; these do **not** substitute for the complete packaged EMHASS test suite, full optimisation, real `publish-data`, or CI on pandas 3.0.6. Prior upstream issue reproduction covers pandas 2.2.3 and 3.0.6, but was not independently repeated as a full package run in this audit.

## Evidence classification and code-path inventory

| ID | Component / upstream line (approx.) | Observation | Disposition |
| --- | --- | --- | --- |
| D1 | `src/emhass/utils.py:201`, `get_forecast_dates` | `tz_convert(time_zone).floor(freq)` rounds in ambiguous local wall-clock time and raises in the repeated hour. | **CONFIRMED defect** (#1189) |
| D2 | `src/emhass/forecast.py:298-314`, `Forecast.__init__` | `nearest` already rounds in UTC; `first` uses local `.floor`, `last` uses local `.ceil`; both independently fail in a fold. | **CONFIRMED producer-path defect / separate coverage needed** |
| D3 | `src/emhass/forecast.py:1883-1920`, `_extract_daily_forecast` | `between_time(first_hour,last_hour)` selects repeated wall-clock times from *both* UTC occurrences. The resulting array may differ in length and can start before the intended UTC instant; `list_and_perfect` has no date filter. | **CONFIRMED downstream shape and alignment defect** |
| D4 | `src/emhass/forecast.py:1930-1977`, forecast alignment | `pd.merge_asof(...direction="nearest")` has no tolerance; a source shifted one hour can attach to unrelated target instants without exception. | **CONFIRMED unsafe primitive; production-path occurrence/magnitude not yet proven** |
| D5 | `src/emhass/forecast.py:840-852`, forecast.solar | Datetime parse then `tz_localize(... ambiguous="infer", nonexistent="shift_forward")`; format, timezone-awareness and preservation of original instant need provider-specific replay. | **Review candidate, not a proved defect** |
| D6 | `src/emhass/retrieve_hass.py:525` and history/index resampling | Some paths parse local ISO8601 offsets without explicit UTC normalisation and resample inputs; mixed-offset and ambiguous/local-naive cases need source-specific tests. | **Review candidate, not a proved defect** |
| D7 | `src/emhass/command_line.py:2700-2755` | Weather/GHI series can have timezone dropped or assigned when indices differ; prove the original instant and values are preserved with DST. | **Review candidate, not a proved defect** |
| D8 | `src/emhass/optimization.py:7180-7225`, perfect forecast | Perfect-forecast code explicitly skips a day whose start/end have differing offsets; determine if intentional, document support and test; do not conflate with naive MPC. | **Observed branch; behavioural contract unresolved** |
| D9 | `src/emhass/command_line.py:3655-3665` | `opt_res_latest.csv` mixed-offset timestamps are now parsed with `utc=True` then converted; addresses earlier #769/#778/#780 class. | **Existing mitigation; retain real publish regression** |
| D10 | `src/emhass/command_line.py:3669+`, `_get_closest_index`, publication and APIs | Selection uses aware now and nearest/fill indexers. Exact same-run plan/publication identity, failed-run stale-data behaviour and first-step UTC alignment remain to be proven. | **End-to-end assurance outstanding** |
| D11 | `src/emhass/machine_learning_forecaster.py:115,171`, calibration/history | Index datetime parsing and new forecast range generation have separate timezone semantics. | **Lower-priority path inventory; test if used, do not assert defect** |
| D12 | `src/emhass/forecast_calibration.py:136,241+` | `normalize()` and calendar-date grouping should be checked on transitions where civil midnight is affected; not an established #1189 reproducer. | **Lower-priority review candidate** |

Other inspected core modules: `connection_manager`, `battery_identification`, `last_run`, `machine_learning_regressor`, `plan_store`, `pv_bias_calibration`, `web_server`, `websocket_client`. No additional direct fall-back rounding defect was established from their exposed date operations; this is **not** an exhaustive control/data-flow proof of each module.

### Isolated, executed reproductions

1. **48 timezone/instant/step combinations:** Europe/Berlin 2026-10-25, Australia/Sydney 2027-04-04, Europe/Dublin 2026-10-25, Africa/Casablanca 2026-02-15; both occurrences of 02:00/02:30 where applicable, `5/15/30 min`. Current local `Timestamp.floor` failed **48/48**, local `ceil` failed **48/48**. Round in UTC then convert preserved the absolute instant without errors in **96/96** operations. These are isolated pandas primitives, not the final patch or an EMHASS suite pass.
2. **Forecast extraction second fold, Berlin 2026-10-25:** desired `2026-10-25T02:00:00+01:00` through 05:00 CET, 30-minute grid = **7** instants; a source grid including the earlier occurrence of 02:00/02:30 CEST returned **9** rows with `between_time("02:00","05:00")`, beginning **one hour earlier in UTC**. Rebuilding a DataFrame against the seven target timestamps raised `ValueError: Shape of passed values is (9, 1), indices imply (7, 1)`. Strict `reindex(target_aware_index)` selected the correct seven instant-specific entries in this fixture.
3. **Nearest-merge false match:** isolated source timestamps shifted **+1 hour** supplied apparently valid price values to target rows under the currently used `merge_asof(...direction="nearest")` without an alignment/tolerance guard.
4. **Six independent UTC-clock reference windows:** Berlin 2026 spring **46** and fall **50** half-hour steps; Sydney 2026 spring **276** and 2027 fall **300** five-minute steps; Lord Howe 2026 spring **47** and 2027 fall **49** half-hour steps. All six reference calendar windows had unique, strictly regular UTC instants and passed. This confirms reference behaviour, not EMHASS output parity.

### Existing tests inspected

- `tests/test_utils.py`: existing local-calendar endpoints, Sydney ambiguity, Casablanca negative DST, spring-horizon extensions; **not sufficient for both *running within* each fold**.
- `tests/test_forecast.py`: existing spring/fall forecast-list horizon-length tests and nearest tie-second test; **not evidence of fold-specific instant/value alignment for all three rounding modes**.
- `tests/test_command_line_utils.py`: Sydney spring short-day 287-row naive MPC regression; **no equivalent full failover-fold end-to-end confirmation found**.
- `tests/test_optimization.py`, `tests/test_web_server.py`, `tests/test_publish_horizon_attributes.py`: inspected named test inventory; **did not identify a dedicated #1189 multi-fold optimiser/CSV/publish test**.
- Existing tests can pass while this issue remains: do not infer complete DST correctness from test totals.

## Historical-regression mapping (retain; avoid duplicates)

- #126: ambiguous time on publish (2023).
- #224 and #248: spring-forward forecast/shape failures (2024).
- #496 and #589: weather-localisation and naive forecast DST failures (2025).
- #769, #778, #780: mixed-offset `opt_res_latest.csv` read/publish issues (2026).
- #1076: clock re-read causing one-step forecast-list race; `get_forecast_days_csv` now reuses frozen forecast start.
- #1163 / PR #1164: spring calendar endpoint, released in 0.18.5.
- #1165 / PR #1166: DST-short horizon extension, released in 0.18.5.
- #1167: separate site-vs-fixed-tariff timezone contract; avoid incorrectly claiming #1189 itself fixes tariff semantics.
- #1189: repeated-hour forecast start plus downstream alignment.

## Implementation direction (candidate, NOT validated patch)

1. Freeze one timezone-aware reference **UTC instant** for a request and derive all forecast starts from it. Apply round/floor/ceil in UTC where interval semantics are elapsed-time aligned; convert only for display/local-calendar boundaries.
2. Retain and regression-test `add_local_calendar_days` civil-day endpoint contract (including gap/fold, Lord Howe, negative DST). **Do not convert civil calendar-day horizons to indiscriminate fixed 24-hour durations.**
3. Replace wall-clock-only `between_time` array extraction in implicated forecast paths with exact timezone-aware datetime-index alignment. If an input is intrinsically positional, define and test one-to-one position→instant contract; do not silently pad, truncate or reorder.
4. Restrict forecast joins to proven intended instants and explicit timesteps; do not use an unlimited nearest match to disguise data misalignment. Preserve existing intended interpolation behaviour where validated.
5. Ensure successful solver and publication refer to the same horizon, same run and actual UTC instants. On failure, do not mislabel a prior plan as fresh. Keep separate bug families (#1190 etc.) separate unless a common invariant justifies a shared patch.

## Exit matrix — not satisfied yet

| Gate | Required evidence | Status |
| --- | --- | --- |
| E1 | All core time operators and affected callers reviewed / dispositioned | **PARTIAL** (source inventory; full call graph not executed) |
| E2 | Both occurrences of fall-back fold; before/through/after; spring gap; 5/15/30-minute modes and nearest/first/last | **PARTIAL** (isolated pandas checks only) |
| E3 | Sydney, Berlin, Lord Howe, Dublin, Casablanca; positive/negative DST | **PARTIAL** |
| E4 | CSV, list, typical, weather, PV, load and price values equal expected **UTC-instant** values | **OPEN** |
| E5 | Strict unique regular UTC index; exact requested horizon; no shape mismatches or silent positional shifts | **OPEN for packaged EMHASS** |
| E6 | `dayahead-optim` and `naive-mpc-optim` solver results across DST | **OPEN for #1189** |
| E7 | `publish-data`, persisted CSV, `/api/v1/plan`, `/api/v1/last-run` same-run and stale-plan failure behaviour | **OPEN for #1189** |
| E8 | Non-DST no-regression, repeatable frozen-clock tests, pandas 2.2.3 **and** 3.0.6 on complete relevant CI | **OPEN** |
| E9 | Independent upstream maintainer/reporter validation; docs/changelog/cookbook as genuinely affected | **OPEN** |
| E10 | Separate site-specific HA/Package18 fail-closed boundary confirmed offline before production use | **OPEN — not a substitute for upstream release acceptance** |

**Required next step:** turn E2-E8 into deterministic tests against the real EMHASS package on the exact proposed commit, implement smallest coherent fix supported by those tests, review, then raise the smallest coordinated, individually reviewable PR set justified by the completed audit and maintainer feedback, all referencing #1189; validate the combined head before requesting a coordinated release. Do not close #1189 on source-only or isolated-primitive evidence. Do not change live HA/EMS, Sigenergy registers (especially protected 41501), or operator mode during this audit.


## 2026-10-12 follow-up: history-ingestion and publication-path qualification

The current source audit was extended beyond initial forecast, optimisation and publication files. Upstream reviewed at tree `224e1e715a2054bb9a1792d49847babc0f18a70f`. Tooling in the isolated analysis environment: Python with pandas 2.2.3 and pytz 2026.2. **Not** an installed EMHASS package, full HA REST/WS runtime, bundled CI, or pandas 3.0.6 validation.

### D13 — Home Assistant REST history mixed-offset resampling: new deterministic reproducer

- Upstream: `src/emhass/retrieve_hass.py::_process_history_dataframe` around lines 498–527.
- The production source first runs `pd.to_datetime(df_raw["last_changed"], format="ISO8601")` (no `utc=True`), assigns that to a DataFrame index and calls `.resample(self.freq).mean()`.
- Isolated 30-minute Berlin 2026-10-25 and Sydney 2027-04-04 fall-back samples, each containing both offsets in one REST-style `last_changed` sequence, produce an **object Index**, followed by `TypeError: Only valid with DatetimeIndex, TimedeltaIndex or PeriodIndex` on resampling. In the same fixtures, parsing with `utc=True` yields four correct 30-minute UTC-indexed rows.
- **Classification:** CONFIRMED failure of the exact parsing/resampling operations **conditional on a single source response containing mixed offsets**. Whether the active REST retrieval call returns this shape on the fall-back date, and whether this belongs in #1189's coordinated PR set or a separately linked upstream issue, remain integration / maintainer-scope questions. Existing #769/#778/#780 fixed *result CSV* reading, not this identified *HA history input* operation. Do not claim this production path is live-broken without the REST fixture integration test.
- Required regression: an actual `RetrieveHass._process_history_dataframe` test for one response with two distinct offsets, verifying original chronological UTC instants and corresponding values plus a non-transition control.

### D14 — Saved entity JSON publication: isolated negative check (avoid false positive)

- `RetrieveHass.post_data` entity-save serialises time-indexed frames using `.to_json(orient="index", date_unit="s", date_format="iso")` around `src/emhass/retrieve_hass.py` entity-save path.
- An isolated Sydney fall-back round-trip using that exact serialization converted keys to distinct UTC `Z` timestamps; `pd.read_json(..., orient="index")` restored a proper UTC datetime index, and `pd.to_datetime(...).tz_convert("Australia/Sydney")` preserved both folds.
- **Classification:** Evidence of a mitigating producer contract, **not** a confirmed new issue in `publish_json`. Preserve this positive-path test and only open a defect if an actual alternate producer creates mixed-offset keys or an end-to-end fixture fails.

### D15 — Site-level dispatch boundary is not a substitute for upstream UTC correctness

Read-only source verification against `MMicieli/home-assistant-config` master tree `3767dd8250f10d06321619135782866c657d7b8e`:

- `scripts/ems_production_runner.py` calls `naive-mpc-optim` then `publish-data`; the `emhass_production_evidence.evaluate_binding` verdict is captured/logged observationally and does **not** change runner exit status.
- `packages/18_ems_production_control_v1.yaml` performs the commissioned physical handback on an unsuccessful runner return code; on a successful return the Package18 dispatch path uses actual published sensor values, operator authority and freshness/validity checks, but **does not independently prove same-cycle plan-to-t0 UTC identity**.
- The existing PV-adjusted validity/freshness gate based on `last_reported` does not itself prove the intended timestamp of every published value; a fresh publication containing a wrong-fold value is logically different from an aged sensor.
- **Classification:** system boundary consequence / *potential* successful-but-shifted-plan exposure; no live incident established, no new Package18 defect or control rewrite asserted. Prior #1189 off-box reproducer showed a plan beginning an hour earlier after an isolated workaround. E10 requires a synthetic, read-only negative test of that scenario, preferably against the current runner and Package18 contract. Do not treat a valid evidence binding as a pre-dispatch gate, and do not add a new bespoke gate without a demonstrated need, site design review and operator approval.
- No changes to existing protected Sigenergy Modbus 41501 policy or site operator intent.

### API/forecast contract details to preserve

- Per upstream `AGENTS.md` and `docs/passing_data.md`: plain runtime lists are positional optimisation timesteps, whereas timestamped mappings carry absolute instants. Do not silently reinterpret list inputs as timestamped mappings; test both contracts separately.
- `get_forecast_days_csv` now reuses `Forecast.start_forecast` and fixes the earlier #1076 double-now boundary race. Keep that freeze-once invariant, including across DST folds.
- `src/emhass/utils.py::_align_runtime_forecast_mapping` already normalises timestamped mappings to UTC before alignment; retain its original value-to-instant contract.
- `src/emhass/command_line.py::_load_opt_res_latest` uses `pd.to_datetime(..., utc=True)` for mixed-offset CSV. Preserve and cover it with true persisted-result -> publication tests.
- Perfect-forecast DST day-skipping, weather/GHI localisation and forecast.solar parsing remain *candidate* reviews, not new confirmed bugs.

### Scope and PR coordination decision

- **Authoritative tracker:** upstream issue #1189; prior investigation comment posted by MMicieli. No redundant implementation issues created.
- **Preferred implementation:** a small coordinated set, typically (1) forecast-start UTC-safe rounding with local-calendar boundaries, (2) forecast extraction and exact alignment, and (3) genuinely necessary combined end-to-end regressions. Each corrective PR includes targeted tests; integration regressions may be in PR 2 if a stand-alone PR 3 would be test-only churn.
- If D13 is independently reproducible against the real EMHASS package and confirmed to need a source change, classify its owning subsystem/issue before adding it to a PR. Avoid bundling unrelated fixes solely because they concern timezones.
- Submit coordinated PRs for upstream review only after reproducible tests and code review. Individual PRs must pass their relevant tests; final integration of the complete set must pass on the combined head. GitHub cannot atomically merge independent PRs. Have the maintainer coordinate rapid sequential merges where feasible; rebase/retarget as necessary. **Do not claim zero rebases or automatic same-release inclusion.**
- The final independent Claude CLI code/test sweep is reserved for **Thursday 2026-10-15 AEDT** when the operator chooses to execute it. See `docs/issue_1189_dst_claude_cli_final_sweep_2026-10-15.md`. The handoff is not a scheduled unattended action.

### Upstream contribution quality contract

Before claiming release-ready: follow `AGENTS.md`, `CONTRIBUTING.md`, `docs/develop.md`, `docs/develop_ai_coders.md`; run canonical `uv sync --extra test`, `uv run pytest tests/`, `uvx ruff check .` and `uvx ruff format --check .` in an actual EMHASS checkout. Test the relevant paths on both supported pandas 2.2.x and 3.0.x where feasible, but respect genuine environment/dependency compatibility failures and report exactly what ran. Explicitly verify unaffected spring fixes #1164/#1166 and the already-fixed CSV publish family #769/#778/#780. Do not mark gates E1–E10 satisfied on source inspection or isolated primitives alone.
