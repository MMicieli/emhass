#!/usr/bin/env python3
"""Offline, synthetic, version-comparable diagnostic for EMHASS issue #1112.

Runs only the optimizer in a temporary checkout. No HA, Modbus, or device APIs.
This is NOT an exact reporter reproduction: #1112 omitted its load forecast.
"""
import argparse
import asyncio
import copy
import json
import logging
import pathlib
import sys
import traceback

import numpy as np
import orjson
import pandas as pd

PV = [
    1158, 248, 118, 43, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 9, 43, 97, 205, 349, 507,
    687, 911, 1163, 1356, 1498, 1587, 1647, 1688, 1735, 1752,
    1583, 1259, 960, 710, 506, 317,
]
N = 48
assert len(PV) == N


async def load_configs(root, logger):
    from emhass import utils

    conf = {
        "data_path": root / "data",
        "root_path": root / "src" / "emhass",
        "defaults_path": root / "src" / "emhass" / "data" / "config_defaults.json",
        "associations_path": root / "src" / "emhass" / "data" / "associations.csv",
    }
    config = await utils.build_config(conf, logger, conf["defaults_path"])
    _, secrets = await utils.build_secrets(conf, logger, no_response=True)
    params = await utils.build_params(conf, secrets, config, logger)
    retrieve, optim, plant = utils.get_yaml_parse(orjson.dumps(params).decode(), logger)
    return conf, retrieve, optim, plant


def configs(base, hybrid):
    conf, retrieve, optim, plant = base
    r, o, p = copy.deepcopy(retrieve), copy.deepcopy(optim), copy.deepcopy(plant)
    r["optimization_time_step"] = pd.Timedelta(minutes=30)
    r["time_zone"] = "Australia/Sydney"
    o.update({
        "delta_forecast_daily": 1,
        "num_threads": 1,
        "lp_solver_timeout": 90,
        "lp_solver_mip_rel_gap": 0.001,
        "set_use_pv": True,
        "set_use_battery": True,
        "set_nocharge_from_grid": False,
        "set_nodischarge_to_grid": True,
        "set_battery_dynamic": True,
        "battery_dynamic_max": 0.9,
        "battery_dynamic_min": -0.9,
        "weight_battery_discharge": 0.0,
        "weight_battery_charge": 0.0,
        "number_of_deferrable_loads": 0,
    })
    for field in (
        "nominal_power_of_deferrable_loads",
        "minimum_power_of_deferrable_loads",
        "operating_hours_of_each_deferrable_load",
        "start_timesteps_of_each_deferrable_load",
        "end_timesteps_of_each_deferrable_load",
        "treat_deferrable_load_as_semi_cont",
        "set_deferrable_load_single_constant",
        "set_deferrable_startup_penalty",
        "is_electric_load",
        "cost_forecast_per_deferrable_load",
        "deferrable_load_max_cost",
        "set_deferrable_max_startups",
        "def_minimum_on_time",
        "def_minimum_off_time",
        "def_load_config",
        "deferrable_load_groups",
    ):
        o[field] = []
    p.update({
        "inverter_is_hybrid": hybrid,
        "compute_curtailment": False,
        "inverter_ac_output_max": 5000,
        "inverter_ac_input_max": 5000,
        "inverter_efficiency_dc_ac": 0.96,
        "inverter_efficiency_ac_dc": 0.96,
        "maximum_power_from_grid": 9000,
        "maximum_power_to_grid": 9000,
        "battery_discharge_power_max": 5000,
        "battery_charge_power_max": 5000,
        "battery_discharge_efficiency": 0.97,
        "battery_charge_efficiency": 0.97,
        "battery_nominal_energy_capacity": 15000,
        "battery_minimum_state_of_charge": 0.2,
        "battery_maximum_state_of_charge": 0.95,
        "battery_target_state_of_charge": 0.5,
        "battery_stress_cost": 0.0,
        "number_of_batteries": 1,
    })
    return conf, r, o, p


def values(var, name, missing):
    if isinstance(var, (list, tuple)):
        return sum((values(x, f"{name}[{i}]", missing) for i, x in enumerate(var)),
                   np.zeros(N, dtype=float))
    if var is None or getattr(var, "value", None) is None:
        missing.append(name)
        return np.full(N, np.nan)
    result = np.asarray(var.value, dtype=float).reshape(-1)
    if result.size != N:
        missing.append(f"{name}: size {result.size}")
        return np.full(N, np.nan)
    return result


def magnitude(array):
    return float(np.max(np.abs(array))) if np.all(np.isfinite(array)) else None


def scenario(base, name, hybrid, costfun, repeat, out):
    from emhass.optimization import Optimization

    conf, r, o, p = configs(base, hybrid)
    logger = logging.getLogger("issue1112")
    t = pd.date_range("2026-01-15", periods=N, freq="30min", tz=r["time_zone"])
    pv = pd.Series(PV, index=t, dtype=float)
    load = pd.Series([870.0] * 25 + [620.0] * 23, index=t)
    input_data = pd.DataFrame(
        {"unit_load_cost": 0.25, "unit_prod_price": 0.07},
        index=t,
    )
    opt = Optimization(r, o, p, "unit_load_cost", "unit_prod_price",
                       costfun, conf, logger)
    records = []
    for attempt in range(2 if repeat else 1):
        label = f"{name}_{attempt+1}" if repeat else name
        res = opt.perform_naive_mpc_optim(
            input_data, pv, load, N,
            soc_init=0.9366, soc_final=0.5,
            def_total_hours=[], def_total_timestep=[],
            def_start_timestep=[], def_end_timestep=[],
        )
        if not isinstance(res, pd.DataFrame) or len(res) != N:
            raise AssertionError(f"{label}: invalid result shape {getattr(res, 'shape', None)}")
        missing = []
        grid_pos = values(opt.vars.get("p_grid_pos"), "p_grid_pos", missing)
        grid_neg = values(opt.vars.get("p_grid_neg"), "p_grid_neg", missing)
        batt_pos = values(opt.vars.get("p_sto_pos"), "p_sto_pos", missing)
        batt_neg = values(opt.vars.get("p_sto_neg"), "p_sto_neg", missing)
        grid = grid_pos + grid_neg
        batt = batt_pos + batt_neg
        def_cols = [c for c in res.columns if c.startswith("P_deferrable")]
        def_sum = res[def_cols].sum(axis=1).to_numpy() if def_cols else np.zeros(N)
        dc_residual = None
        conv_residual = None
        if hybrid:
            h = values(opt.vars.get("p_hybrid_inverter"), "p_hybrid_inverter", missing)
            dc_ac = values(opt.vars.get("p_dc_ac"), "p_dc_ac", missing)
            ac_dc = values(opt.vars.get("p_ac_dc"), "p_ac_dc", missing)
            ac_residual = h + grid - load.to_numpy() - def_sum
            dc_residual = pv.to_numpy() + batt - dc_ac + ac_dc
            conv_residual = h - (dc_ac * 0.96 - ac_dc / 0.96)
        else:
            ac_residual = pv.to_numpy() + batt + grid - load.to_numpy() - def_sum
        status = str(opt.optim_status)
        solver_status = str(getattr(getattr(opt, "prob", None), "status", None))
        residuals = [magnitude(ac_residual), magnitude(dc_residual)
                     if hybrid else None, magnitude(conv_residual) if hybrid else None]
        good = (
            status.startswith("Optimal")
            and solver_status in ("optimal", "optimal_inaccurate")
            and not missing
            and all(v is None or v <= 1.0 for v in residuals)
            and np.allclose(res["P_PV"], pv)
            and np.allclose(res["P_Load"], load)
            and np.allclose(res["P_grid"], grid, atol=1.0)
            and np.allclose(res["P_batt"], batt, atol=1.0)
        )
        record = {
            "scenario": label, "costfun": costfun, "inverter_is_hybrid": hybrid,
            "status": status, "cvxpy_status": solver_status,
            "missing_raw_values": missing,
            "max_abs_ac_balance_residual_w": residuals[0],
            "max_abs_dc_balance_residual_w": residuals[1],
            "max_abs_converter_residual_w": residuals[2],
            "nonzero_grid_steps": int(np.count_nonzero(np.abs(res["P_grid"]) > 1)),
            "nonzero_battery_steps": int(np.count_nonzero(np.abs(res["P_batt"]) > 1)),
            "soc_end": float(res["SOC_opt"].iloc[-1]),
            "verdict": "PASS" if good else "FAIL",
        }
        result = res.copy()
        result["diagnostic_ac_residual_w"] = ac_residual
        if hybrid:
            result["diagnostic_dc_residual_w"] = dc_residual
            result["diagnostic_converter_residual_w"] = conv_residual
        result.to_csv(out / f"{label}.csv", index_label="timestamp")
        records.append(record)
        print(json.dumps(record, indent=2), flush=True)
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("issue1112")
    logger.setLevel(logging.WARNING)
    logger.addHandler(logging.StreamHandler())
    report = {
        "issue": "https://github.com/davidusb-geek/emhass/issues/1112",
        "load": "SYNTHETIC: 870W steps 0..24, 620W steps 25..47",
        "pv": "Issue #1112's exact 48-point list",
        "price": "SYNTHETIC flat import 0.25/export 0.07",
        "scenarios": [], "errors": [],
    }
    try:
        base = asyncio.run(load_configs(args.repo.resolve(), logger))
        for name, hybrid, costfun, repeat in (
            ("hybrid_selfcons_repeated", True, "self-consumption", True),
            ("nonhybrid_selfcons", False, "self-consumption", False),
            ("hybrid_profit", True, "profit", False),
        ):
            try:
                report["scenarios"] += scenario(
                    base, name, hybrid, costfun, repeat, out,
                )
            except Exception:
                report["errors"].append({"scenario": name, "traceback": traceback.format_exc()})
                print(report["errors"][-1]["traceback"], file=sys.stderr)
    except Exception:
        report["errors"].append({"scenario": "setup", "traceback": traceback.format_exc()})
        print(report["errors"][-1]["traceback"], file=sys.stderr)
    (out / "summary.json").write_text(json.dumps(report, indent=2))
    failed = bool(report["errors"] or any(
        r["verdict"] != "PASS" for r in report["scenarios"]
    ) or len(report["scenarios"]) != 4)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
