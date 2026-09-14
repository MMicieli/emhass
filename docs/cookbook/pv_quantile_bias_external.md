# External PV P10 quantile bias + calibration

## Goal

Feed EMHASS a P10 (conservative) companion for a PV forecast you already obtain outside EMHASS (e.g. from a Home Assistant integration), blend it toward the plan with `weather_forecast_pv_quantile_bias`, and get a data-driven bias recommendation from your own logged history via the `pv-bias-calibration` action — without ever letting EMHASS apply that recommendation automatically.

## Prerequisites

- EMHASS version: ≥ the release containing issue #1128 (caller-supplied PV P10 for external forecasts and bias calibration).
- `weather_forecast_method: list` (external forecast path) — this recipe does not touch native Solcast acquisition.
- A source of both P50 and P10 PV forecast values in Watts, same timeline (e.g. a Home Assistant integration that already fetches Solcast, forecast.solar, or another provider and exposes both percentiles).
- Transport stack tested against: direct `curl` / `rest_command` to the EMHASS `/action/*` HTTP endpoints, Home Assistant Core ≥ 2024.x. Node-RED / AppDaemon equivalents are untested — contribution welcome.

## Step 1: Verify your static EMHASS config

<!-- source: src/emhass/static/data/param_definitions.json:304 -->

You do not need to change `weather_forecast_method` in the static config if you already pass it at runtime with your forecast; if you set it statically, use `list`:

```yaml
weather_forecast_method: list
weather_forecast_pv_quantile_bias: 0.0   # start at 0 (pure P50); tune later
```

Expected: your EMHASS instance restarts cleanly with these keys present. `weather_forecast_pv_quantile_bias` defaults to `0.0` (an exact P50 no-op) if omitted.

## Step 2: Pass P50 + P10 as a plain list

<!-- source: src/emhass/utils.py:2011 -->
<!-- transport: Home Assistant rest_command / curl (tested) -->

`pv_power_forecast_p10` is a *companion* to `pv_power_forecast`: it is rejected with a logged error if `pv_power_forecast` is not also supplied and valid in the same call. Both arrays must be the same length as your forecast horizon and are in Watts.

```bash
curl -i -H 'Content-Type:application/json' -X POST -d '{
    "pv_power_forecast": [0, 0, 120, 800, 1500, 2100, 1800, 900, 100, 0],
    "pv_power_forecast_p10": [0, 0, 40, 300, 700, 1100, 900, 350, 20, 0],
    "weather_forecast_pv_quantile_bias": 0.5
}' http://localhost:5000/action/dayahead-optim
```

Expected: the plan is built against `0.5 * P10 + 0.5 * P50` per timestep instead of pure P50 — check the returned plan's PV column against the arithmetic midpoint of the two arrays above.

## Step 3: Pass P50 + P10 as timestamped values

<!-- source: src/emhass/utils.py:1226 (treat_runtimeparams / _align_timestamped_forecast_to_grid) -->
<!-- transport: Home Assistant rest_command (tested) -->

If your integration exposes forecasts as timestamp -> value maps instead of plain lists, pass both keys the same way; EMHASS aligns `pv_power_forecast_p10` onto the optimization grid with the exact same alignment/resampling machinery as `pv_power_forecast` (no second implementation, no drift between the two series).

```yaml
rest_command:
  dayahead_optim_p10:
    url: http://localhost:5000/action/dayahead-optim
    method: post
    content_type: application/json
    payload: >
      {
        "pv_power_forecast": {
          "{{ now().replace(hour=6, minute=0, second=0).isoformat() }}": 0,
          "{{ now().replace(hour=7, minute=0, second=0).isoformat() }}": 120,
          "{{ now().replace(hour=8, minute=0, second=0).isoformat() }}": 800
        },
        "pv_power_forecast_p10": {
          "{{ now().replace(hour=6, minute=0, second=0).isoformat() }}": 0,
          "{{ now().replace(hour=7, minute=0, second=0).isoformat() }}": 40,
          "{{ now().replace(hour=8, minute=0, second=0).isoformat() }}": 300
        },
        "weather_forecast_pv_quantile_bias": 0.5
      }
```

Expected: identical behaviour to Step 2 once EMHASS resamples/aligns the two maps onto the same grid. A misaligned, too-short, or non-finite `pv_power_forecast_p10` is rejected explicitly (check the EMHASS log for an error) rather than silently substituted.

## Step 4: Get a bias recommendation from your own history

<!-- source: src/emhass/command_line.py:3081 (pv_bias_calibration action wrapper) -->
<!-- source: src/emhass/pv_bias_calibration.py:compute_pv_bias_calibration -->
<!-- transport: curl / Home Assistant rest_command (tested) -->

Once you have logged a history of `(P10, P50, realised PV)` — e.g. one entry per day — call the `pv-bias-calibration` action to get a recommended `weather_forecast_pv_quantile_bias`. `p10`, `p50` and `actual` are required and must be equal-length, ordered, and mostly finite.

```bash
curl -i -H 'Content-Type:application/json' -X POST -d '{
    "p10": [1.8, 2.1, 0.9, 3.0, 2.5],
    "p50": [4.2, 4.8, 2.1, 6.1, 5.0],
    "actual": [3.9, 5.0, 0.5, 6.5, 4.8],
    "target_shortfall_rate": 0.10
}' http://localhost:5000/action/pv-bias-calibration
```

Expected: an HTTP 200 with a JSON body containing `recommended_bias`, `achieved_shortfall_rate`, `feasible_shortfall_range`, `target_feasible`, `converged`, `n_observations`, and the full `bias_trajectory`/`static_shortfall_curve` diagnostics.

## Step 5: Exclude curtailed steps from the calibration

<!-- transport: curl (tested) -->

If your system curtails (export limiting, inverter clipping), flag those steps so they are dropped rather than misread as forecast shortfalls. Never derive this flag from how far `actual` fell below the forecast — pass an independent curtailment signal.

```bash
curl -i -H 'Content-Type:application/json' -X POST -d '{
    "p10": [1.8, 2.1, 0.9, 3.0, 2.5],
    "p50": [4.2, 4.8, 2.1, 6.1, 5.0],
    "actual": [3.9, 5.0, 0.5, 6.5, 4.8],
    "curtailed": [false, false, false, true, false],
    "curtailment_margin": 1
}' http://localhost:5000/action/pv-bias-calibration
```

Expected: `n_curtailed_excluded` in the response reflects the dropped step(s); `curtailed_fraction` shows how much of your history that represents.

## Interpretation / troubleshooting

- **`target_feasible: false` / `converged: false`**: your `target_shortfall_rate` sits outside `feasible_shortfall_range` for this history — no convex P10-P50 blend can reach it (EMHASS never extrapolates below P10). Pick a target inside the reported range, or accept the closest achievable rate.
- **Large `curtailed_fraction` with a warning logged**: a large share of your history was dropped as curtailed; curtailment concentrates on high-production steps, so treat the recommendation as indicative of the non-curtailed regime rather than the site as a whole.
- **`pv_power_forecast_p10` silently has no effect**: check the EMHASS log for a rejection message (missing `pv_power_forecast`, wrong length, or non-finite values) — the companion never falls back to a fabricated value, it is dropped and P50-only behaviour resumes.

## Caveats

- **The recommendation is never applied automatically.** `pv-bias-calibration` is a reporting/recommendation action only — it does not modify `weather_forecast_pv_quantile_bias`, your configuration, the live PV forecast, or run an optimization. Setting `weather_forecast_pv_quantile_bias` to the recommended value is a separate, explicit step you take yourself.
- `pv_power_forecast_p10` only has an effect when `weather_forecast_method` is `list` (this recipe) or `solcast` (native P10); it is ignored with a logged warning for any other method.
- This is a deterministic linear blend of two point forecasts, not a probabilistic forecast model — do not read `feasible_shortfall_range` or `achieved_shortfall_rate` as a guarantee about future weather, only as a description of the logged history you fed in.
- No P90 handling, no scenario/stochastic optimisation, and no automatic battery reserve/SOC policy are part of this feature — see `docs/forecasts.md` for the full scope boundary.

## Credits

- Issue davidusb-geek/emhass#1128 (Phase 1 follow-up: caller-supplied PV P10 for external forecasts and bias calibration), building on #841, #961 and #1043.
- Field names verified against `src/emhass/utils.py:treat_runtimeparams` and `src/emhass/command_line.py:pv_bias_calibration` on 2026-09-14.
