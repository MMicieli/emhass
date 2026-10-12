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


## 2026-10-12 independent reporter update — upstream issue #1189

**Primary source:** [@thorstenhornung1's independent validation and pytest-suite offer](https://github.com/davidusb-geek/emhass/issues/1189#issuecomment-6117603789). The reporter states they ran frozen-clock black-box EMHASS v0.18.5 tests offline; **these are reporter-reproduced observations and are not yet locally rerun using the shared suite**. Relevant upstream source constructs were inspected against reviewed master tree `224e1e715a2054bb9a1792d49847babc0f18a70f` unless the CLI revalidates against a newer commit. Do not overstate local verification.

| ID | Finding reported by upstream independent tester | Confirming source anchor / scope | Classification and next evidence |
| --- | --- | --- | --- |
| **D16** | **Dual request time grids on ordinary days**: `get_forecast_dates` unconditionally local-floors, `Forecast.__init__` uses `method_ts_round`. Runtime timestamped mapping is aligned by instant to one grid then used positionally on the other. On Berlin 30 min at 10:20 (`nearest`), 10:00 forecast value can be assigned to 10:30; `last` mismatches most off-boundary runs. Short-DST-day extension may count wrong grid (Berlin March 28 02:30, 60 min -> 23 vs 24). | `utils.py::get_forecast_dates`; `forecast.py::Forecast.__init__`; `utils._align_runtime_forecast_mapping`; runtime parameter preparation. | **Reported real-package defect, code-path divergence independently verified by inspection**. Prioritise over mere DST exceptions because it affects normal operation. Require instant-labelled inputs and full actual-package assertions. |
| **D17** | `pd.Timestamp.replace(microsecond=0)` can re-localise a pytz wall time to a different repeated-hour pass, changing the absolute instant even before round/floor/ceil; negative-DST can reverse selected fold. | `utils.py::get_forecast_dates`; `forecast.py::Forecast.__init__` and calendar endpoint replacement. | **Reported reproduction; source uses replace**. Test second-fold, negative-DST, and subsecond frozen-clock starts; do not assume replace is harmless. |
| **D18** | `_extract_daily_forecast` has **two** different fold-selection failures: MPC may drop rows when first pass's starting wall time excludes second-pass rows with earlier wall time (44 vs 45); perfect-optim may include both passes (our 9-vs-7 isolated example). | `forecast.py::_extract_daily_forecast` multiple `between_time` branches. | **Reporter reproduced MPC; our isolated perfect-style reproduction confirmed other mechanism**. Distinguish failure-specific tests, preserve exact (UTC,value) identity. |
| **D19** | `merge_asof(direction="nearest")` in full EMHASS run silently holds a load-cost CSV's final value for two further hours without warning. | `forecast.py::get_forecast_out_from_csv_or_list` no tolerance. | **Full-run reported confirmation of D4**; decide deliberate forecast-extrapolation policy and test it explicitly, not an arbitrary global tolerance. |
| **D20** | Dublin and Casablanca MPC price-list horizons that include the second pass can raise `KeyError`; pandas 3 `astype("datetime64[ns, <tz>]")` yields zoneinfo index while `.loc` is on pytz index. | `forecast.py::get_forecast_out_from_csv_or_list` index `.astype(df_final.index.dtype)`; `_extract_daily_forecast` `.loc[fcst_index]`. | **Reporter real-package reproduction**; test zoneinfo/pytz index identity and maintain absolute-instant semantics without type-coercion side effects. |
| **D21** | CSV with local ISO timestamps spanning a DST change can raise `Mixed timezones detected`. | `forecast.py::_load_forecast_data` first attempts `pd.to_datetime(first_col, errors="coerce")` without `utc=True`; later parse explicitly uses `utc=True`. | **Reporter reproduced; problematic preflight source confirmed**. Include CSV with two different UTC offsets and test detection/parse contract. |
| **D22** | Open-Meteo's additional UTC rounding can collapse grid for `Asia/Kathmandu` at 30/60-min frequencies under `first`/`last`. | `forecast.py` weather path re-rounds `data.index.tz_convert("utc").round(freq=data.index.freq)` rather than keeping request's canonical aligned grid. | **Reporter real-package reproduction; repeated rounding source confirmed**. Include non-hour-offset timezone and exact UTC grid uniqueness/value identity. |
| **D23** | `publish-data` traverses `get_forecast_dates` even when it consumes an already saved optimisation plan. | Shared input setup in `command_line.py`; `utils.get_forecast_dates`. | **Reporter confirmed route**; regression must show publication selects saved-plan timestamps independently of new request grid, rather than fixing only MPC. |

### Independent black-box suite offered — acquire before duplicate implementation

Reporter has offered to push or attach a **pytest black-box suite** using real `set_input_data_dict`, `prepare_forecast_and_weather_data`, `naive_mpc_optim`, and `publish_data`, frozen using `time-machine` and **no network or EMHASS patches**. Coverage reportedly spans Berlin, Sydney, Dublin, Casablanca (Ramadan), Lord Howe, Kathmandu; 5/15/30/60-min intervals; first/last/nearest modes; both fall-back passes, spring gap, preceding day and normal days; positional lists, timestamped mappings, CSV, Open-Meteo cache and publication. Each forecast value identifies its UTC instant; cases already failing upstream master are strict `xfail` and should become `XPASS` as fixes land. It reportedly also checks historical v0.18.4 behaviour that PRs #1164/#1166 remedied.

**The suite has NOT yet been provided in the issue comment, retrieved, run or independently validated by us.** Proactively ask the reporter for their branch/attachment, inspect licence/test dependencies/security and adapt/reuse high-value tests before duplicating them. Use tests to independently corroborate claims; don't make `time-machine` a new production dependency. Once integrated, replace justified `xfail` with passing strict regressions on the candidate branch while retaining upstream-master failing baselines.

### Design implication for coordinated PRs

The earlier two-PR split is provisional. **D16 suggests a single canonical per-run UTC-instant time grid** consumed consistently by request parameters, `Forecast`, optimiser and publication; however this is a **design option**, not an approved refactor. Investigate the smallest coherent correction compatible with plain positional list forecasts versus UTC-timestamped mappings, local-calendar-day horizons, legacy forecast fallback/extrapolation behaviour, and the source's `method_ts_round` contract. Do not force independent PR boundaries if they would leave interim commits with silently wrong normal-day prices. Co-test and coordinate dependent PRs; no premature upstream submission or live HA/EMS change.

Immediate priority: reporter's offered suite; D16 (ordinary-day economic correctness), D17/D18/D20 (fold identity), D19/D21/D22 (forecast transport), D23 (saved-plan publication). Keep other conditional D5–D15 candidates properly classified rather than assuming all are new production defects.



## 2026-10-12 follow-on independent isolated check — D16, D17 and D22

**Execution:** Python 3.13.5 host, **pandas 2.2.3**, **pytz 2026.2**; no installed EMHASS package, no full optimiser, no live HA, no reporter-supplied test suite. The upstream source constructs were verified on tree `224e1e715a2054bb9a1792d49847babc0f18a70f`; each result below is a *primitive reproduction*, not a complete packaged EMHASS test.

1. **D16 normal-day divergence:** At `2026-10-12 10:20 Europe/Berlin`, 30-minute `get_forecast_dates` start rule `floor` yields `10:00+02:00`, while `Forecast.__init__` default `nearest` UTC-round yields `10:30+02:00`. The source implementation of `utils._align_runtime_forecast_mapping` converts mapping timestamps to UTC, reindexes them to `get_forecast_dates`'s grid and returns **a list**, without preserving time labels for later positional assignment. The *full MPC economic impact* is independently reported in the upstream comment, not reproduced by this isolated test.
2. **D17 incorrect fold after `.replace(microsecond=0)`:** Constructed both folds of `02:30:00.000123` for Berlin 2026-10-25 and Sydney 2027-04-04, and both folds of `01:30:00.000123` Dublin 2026-10-25 and `02:30:00.000123` Casablanca 2026-02-15. Compared local `x.replace(microsecond=0)` to **UTC-first truncation followed by timezone conversion**, rather than comparing with the original fractional-second instant. In 4/8 test cases, local replacement silently changed the selected UTC instant: **-60 minutes** for Berlin/Sydney in the affected occurrence, and **+60 minutes** for Dublin/Casablanca. The other 4/8 preserved the intended occurrence. This directly reproduces a mechanism of first-step UTC misalignment.
3. **D22 redundant weather UTC rounding on non-hour-offset grid:** A 12-element `Asia/Kathmandu` grid beginning local `2026-10-12 10:00`, stepping **30 min**, changed **all 12/12 timestamps** when subjected to the weather-path operation `index.tz_convert('UTC').round('30min').tz_convert(zone)`, with **5 duplicate values**. For 60-min steps 12/12 stamps also changed (no duplicates in this particular 12-slot sample); at 5 or 15 min the 12-slot Kathmandu grid was unaffected. Additional 12-slot control grids at 5/15/30/60 min in `Europe/Berlin` were unaffected by the same UTC rounding. This demonstrates why the already-constructed forecast grid should not be rounded again without validating alignment.
4. **D21 version-specific CSV preflight caveat:** On pandas 2.2.3, `pd.to_datetime(mixed_offset_iso_series, errors='coerce')` produced an object dtype and **FutureWarning**, rather than raising; adding `utc=True` produced `datetime64[ns, UTC]` with both rows. The reporter reports `Mixed timezones detected` in their environment. **Do not label D21 a locally reproduced exception on pandas 2.2.3**; explicitly test the actual EMHASS CSV preflight under pandas 3.0.6 and follow its source-format contract.

**Reproduction recipe for downstream CLI:** use frozen/site-specific aware pandas timestamps; for D17 assert `replace(microsecond=0)` result equals `tz_convert('UTC').replace(microsecond=0).tz_convert(site_tz)` in **absolute UTC nanoseconds**; for D22 assert equality to the original index, `is_unique`, monotonicity and constant elapsed step. For D16 build labelled timestamped mappings with distinguishable values for adjacent 30-min slots, then assert returned forecast/optimiser values match the exact selected UTC instant and not the mapping's list position. Repeat on pandas 3.0.6 using an actual packaged EMHASS run and reporter's external suite if supplied.

**Reporter collaboration blocker:** The issue's latest comment offers a black-box pytest suite but supplies no branch or attachment URL as of this check. Attempting to request it through the current GitHub integration returned HTTP **403 Resource not accessible by integration**, so no comment was posted. Operator can manually ask for a public branch or attachment; do not imply that the request is already published. No upstream PR, deployment or production code change was made.

