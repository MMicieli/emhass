"""UNEXECUTED (no numpy/cvxpy runtime in the authoring container).

Default-OFF parity: run this SAME stand-alone script in an upstream-master checkout and in the
feature branch, then diff the JSON. With the feature absent, variables, constraint structure,
objective and results must be identical.

    cd <emhass checkout> && python parity_dump.py > parity_<label>.json
    diff parity_master.json parity_branch.json && echo EXACT_PARITY

Scalar-only configurations (incl. a production-like hybrid one). No feature parameter is set.
"""

import hashlib
import json
import logging
import pathlib

import pandas as pd

from emhass.optimization import Optimization

ROOT = pathlib.Path.cwd()


def builder(plant_overrides, optim_overrides=None, n_hours=24):
    log = logging.getLogger("parity")
    log.handlers = [logging.NullHandler()]
    retrieve = {
        "optimization_time_step": pd.to_timedelta(30, "minutes"),
        "time_zone": "Europe/Tallinn",
        "sensor_power_photovoltaics": "pv",
        "sensor_power_load_no_var_loads": "load",
    }
    optim = {
        "delta_forecast_daily": pd.Timedelta(hours=n_hours), "num_threads": 0, "set_use_battery": True,
        "set_use_pv": True, "set_total_pv_sell": False, "set_nocharge_from_grid": False,
        "set_nodischarge_to_grid": False, "set_battery_dynamic": False, "set_battery_first_priority": False,
        "battery_dynamic_max": 0.9, "battery_dynamic_min": -0.9, "weight_battery_discharge": 0.0,
        "weight_battery_charge": 0.0, "battery_soc_deficit_threshold": 0.2, "battery_soc_deficit_cost": 0.0,
        "battery_soc_surplus_threshold": 0.9, "battery_soc_surplus_cost": 0.0, "number_of_deferrable_loads": 0,
        "nominal_power_of_deferrable_loads": [], "treat_deferrable_load_as_semi_cont": [],
        "set_deferrable_load_single_constant": [], "set_deferrable_startup_penalty": [],
        "operating_hours_of_each_deferrable_load": [], "start_timesteps_of_each_deferrable_load": [],
        "end_timesteps_of_each_deferrable_load": [], "lp_solver_timeout": 45, "lp_solver_mip_rel_gap": 0,
    }
    optim.update(optim_overrides or {})
    plant = {
        "inverter_is_hybrid": True, "inverter_ac_output_max": 15000, "inverter_ac_input_max": 15000,
        "inverter_efficiency_dc_ac": 1.0, "inverter_efficiency_ac_dc": 1.0, "compute_curtailment": False,
        "maximum_power_from_grid": 50000, "maximum_power_to_grid": 50000,
        "battery_discharge_power_max": 5000, "battery_charge_power_max": 5000,
        "battery_minimum_state_of_charge": 0.05, "battery_maximum_state_of_charge": 0.95,
        "battery_target_state_of_charge": 0.5, "battery_nominal_energy_capacity": 10000,
        "battery_discharge_efficiency": 0.95, "battery_charge_efficiency": 0.95,
        "battery_stress_cost": 0.0, "battery_stress_segments": 10,
    }
    plant.update(plant_overrides)
    return Optimization(
        retrieve, optim, plant, "unit_load_cost", "unit_prod_price", "profit",
        {"root_path": ROOT / "src" / "emhass", "data_path": ROOT / "data"}, log, opt_time_delta=n_hours,
    )


CONFIGS = {
    "default_unity": {},
    "scalar_0.95": {"inverter_efficiency_dc_ac": 0.95, "inverter_efficiency_ac_dc": 0.95},
    "production_like": {  # production values: ac_dc 0.97, dc_ac 0.983, battery 0.9747, 15 kW
        "inverter_efficiency_dc_ac": 0.983, "inverter_efficiency_ac_dc": 0.97,
        "battery_charge_efficiency": 0.9747, "battery_discharge_efficiency": 0.9747,
        "battery_nominal_energy_capacity": 32240, "battery_charge_power_max": 16000,
        "battery_discharge_power_max": 15000,
    },
    "derating_scalar": {"battery_charge_power_derating": [[0.5, 0.4], [0.9, 0.2]]},
    "two_batteries_scalar": {
        "number_of_batteries": 2,
        **{k: [v, v] for k, v in {
            "battery_discharge_power_max": 3000, "battery_charge_power_max": 3000,
            "battery_minimum_state_of_charge": 0.05, "battery_maximum_state_of_charge": 0.95,
            "battery_target_state_of_charge": 0.5, "battery_nominal_energy_capacity": 5000,
            "battery_discharge_efficiency": 0.95, "battery_charge_efficiency": 0.95}.items()},
    },
}


def frame(n=48):
    idx = pd.date_range("2026-03-04", periods=n, freq="30min", tz="Europe/Tallinn")
    price = [0.05 if 4 <= (i % 48) // 2 < 8 else 0.45 if 17 <= (i % 48) // 2 < 21 else 0.2 for i in range(n)]
    df = pd.DataFrame(index=idx)
    df["unit_load_cost"], df["unit_prod_price"] = price, [0.04] * n
    pv = pd.Series([max(0.0, 4000 * (1 - abs((i % 48) - 24) / 12)) for i in range(n)], index=idx)
    load = pd.Series([400 + 600 * ((i % 48) // 2 in (7, 8, 18, 19, 20)) for i in range(n)], index=idx)
    return df, pv, load


out = {}
for name, plant in CONFIGS.items():
    opt = builder(plant)
    df, pv, load = frame()
    n_batt = int(plant.get("number_of_batteries", 1))
    soc = [0.4] * n_batt if n_batt > 1 else 0.4
    res = opt.perform_dayahead_forecast_optim(df, pv, load, soc_init=soc, soc_final=soc)
    out[name] = {
        "status": opt.optim_status,
        "n_variables": len(opt.prob.variables()),
        "variable_names": sorted(v.name() for v in opt.prob.variables()),
        "n_constraints": len(opt.prob.constraints),
        "objective_sha256": hashlib.sha256(str(opt.prob.objective).encode()).hexdigest(),
        "results_sha256": hashlib.sha256(res.round(9).to_csv().encode()).hexdigest(),
        "opt_value": float(opt.prob.value),
    }
print(json.dumps(out, indent=1, sort_keys=True))
