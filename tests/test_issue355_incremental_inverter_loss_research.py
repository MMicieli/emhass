#!/usr/bin/env python
"""Research-only tests for issue #355 incremental inverter-loss spike."""

import logging
import pathlib
import unittest

import numpy as np
import pandas as pd

from emhass.optimization import Optimization

TEST_ROOT = pathlib.Path(__file__).resolve().parents[1]


def build_opt(*, research=False, tolerance=30, plant_overrides=None, optim_overrides=None):
    logger = logging.getLogger("issue355_research_test")
    logger.handlers = []
    logger.addHandler(logging.NullHandler())

    retrieve = {
        "optimization_time_step": pd.to_timedelta(30, "minutes"),
        "time_zone": "Australia/Sydney",
        "sensor_power_photovoltaics": "pv",
        "sensor_power_load_no_var_loads": "load",
    }
    optim = {
        "delta_forecast_daily": pd.Timedelta(hours=5),
        "num_threads": 0,
        "set_use_battery": True,
        "set_use_pv": True,
        "set_total_pv_sell": False,
        "set_nocharge_from_grid": False,
        "set_nodischarge_to_grid": False,
        "set_battery_dynamic": False,
        "set_battery_first_priority": False,
        "battery_dynamic_max": 0.9,
        "battery_dynamic_min": -0.9,
        "weight_battery_discharge": 0.0,
        "weight_battery_charge": 0.0,
        "battery_soc_deficit_threshold": 0.2,
        "battery_soc_deficit_cost": 0.0,
        "battery_soc_surplus_threshold": 0.9,
        "battery_soc_surplus_cost": 0.0,
        "number_of_deferrable_loads": 0,
        "nominal_power_of_deferrable_loads": [],
        "treat_deferrable_load_as_semi_cont": [],
        "set_deferrable_load_single_constant": [],
        "set_deferrable_startup_penalty": [],
        "operating_hours_of_each_deferrable_load": [],
        "start_timesteps_of_each_deferrable_load": [],
        "end_timesteps_of_each_deferrable_load": [],
        "lp_solver_timeout": 45,
        "lp_solver_mip_rel_gap": 0,
    }
    if research:
        optim["research_issue355_incremental_inverter_loss"] = True
        optim["research_issue355_pwl_max_error_w"] = tolerance
    if optim_overrides:
        optim.update(optim_overrides)

    plant = {
        "inverter_is_hybrid": True,
        "compute_curtailment": False,
        "inverter_ac_output_max": 15000,
        "inverter_ac_input_max": 15000,
        "inverter_efficiency_dc_ac": 0.983,
        "inverter_efficiency_ac_dc": 0.97,
        "maximum_power_from_grid": 50000,
        "maximum_power_to_grid": 50000,
        "battery_discharge_power_max": 5000,
        "battery_charge_power_max": 5000,
        "battery_minimum_state_of_charge": 0.1,
        "battery_maximum_state_of_charge": 0.9,
        "battery_target_state_of_charge": 0.5,
        "battery_nominal_energy_capacity": 10000,
        "battery_discharge_efficiency": 0.9747,
        "battery_charge_efficiency": 0.9747,
        "battery_stress_cost": 0.0,
        "battery_stress_segments": 10,
    }
    if plant_overrides:
        plant.update(plant_overrides)

    emhass = {
        "root_path": TEST_ROOT / "src" / "emhass",
        "data_path": TEST_ROOT / "data",
    }
    return Optimization(
        retrieve,
        optim,
        plant,
        "unit_load_cost",
        "unit_prod_price",
        "profit",
        emhass,
        logger,
        opt_time_delta=5,
    )


def scenario(*, pv, load, buy=0.30, sell=0.05):
    n = len(pv)
    index = pd.date_range("2026-09-23", periods=n, freq="30min", tz="Australia/Sydney")
    df = pd.DataFrame(
        {
            "unit_load_cost": np.full(n, buy, dtype=float),
            "unit_prod_price": np.full(n, sell, dtype=float),
        },
        index=index,
    )
    return df, pd.Series(pv, index=index), pd.Series(load, index=index)


def solve(opt, df, pv, load, soc_init=0.5, soc_final=0.5):
    return opt.perform_dayahead_forecast_optim(
        df, pv, load, soc_init=soc_init, soc_final=soc_final
    )


class TestIssue355ResearchSpike(unittest.TestCase):
    def test_feature_off_explicit_false_matches_absent(self):
        df, pv, load = scenario(
            pv=[0, 0, 500, 3000, 4500, 3200, 800, 0, 0, 0],
            load=[600, 550, 600, 700, 900, 1100, 1000, 800, 700, 650],
        )
        a = build_opt()
        b = build_opt(
            optim_overrides={"research_issue355_incremental_inverter_loss": False}
        )
        ra = solve(a, df, pv, load, 0.4, 0.5)
        rb = solve(b, df, pv, load, 0.4, 0.5)

        self.assertEqual(a.optim_status, b.optim_status)
        self.assertAlmostEqual(a.prob.value, b.prob.value, delta=1e-8)
        for col in ("P_batt", "SOC_opt", "P_grid", "P_hybrid_inverter"):
            np.testing.assert_allclose(ra[col], rb[col], atol=1e-7, rtol=0)
        self.assertNotIn("issue355_candidate_loss_W", ra.columns)
        self.assertNotIn("issue355_pwl_delta", a.vars)

    def test_pv_only_is_exact_noop(self):
        df, pv, load = scenario(
            pv=[0, 100, 250, 500, 1000, 2000, 3000, 5000, 8000, 12000],
            load=[700] * 10,
        )
        plant = {"battery_discharge_power_max": 0, "battery_charge_power_max": 0}
        base = build_opt(plant_overrides=plant)
        research = build_opt(research=True, plant_overrides=plant)
        rb = solve(base, df, pv, load)
        rr = solve(research, df, pv, load)

        np.testing.assert_allclose(
            rr["P_hybrid_inverter"], rb["P_hybrid_inverter"], atol=1e-6, rtol=0
        )
        np.testing.assert_allclose(rr["issue355_candidate_loss_W"], 0.0, atol=1e-6, rtol=0)
        np.testing.assert_allclose(rr["issue355_extra_loss_W"], 0.0, atol=1e-6, rtol=0)

    def test_charging_path_is_unchanged(self):
        df, pv, load = scenario(pv=[0] * 10, load=[500] * 10, buy=-0.10, sell=0.0)
        base = build_opt()
        research = build_opt(research=True)
        rb = solve(base, df, pv, load, 0.4, 0.6)
        rr = solve(research, df, pv, load, 0.4, 0.6)

        self.assertTrue(np.any(rb["P_batt"].to_numpy() < -1.0))
        self.assertTrue(np.any(rr["P_batt"].to_numpy() < -1.0))
        np.testing.assert_allclose(
            rr.loc[rr["P_batt"] < -1.0, "issue355_extra_loss_W"],
            0.0,
            atol=1e-6,
            rtol=0,
        )

    def test_night_discharge_uses_incremental_pwl_loss(self):
        df, pv, load = scenario(pv=[0] * 10, load=[2500] * 10, buy=0.45, sell=0.0)
        opt = build_opt(research=True, tolerance=30)
        result = solve(opt, df, pv, load, 0.7, 0.6)

        dis = result["P_batt"].to_numpy() > 1.0
        self.assertTrue(np.any(dis))
        x, y = opt._issue355_research_pwl_points()
        expected_loss = (
            np.interp(result.loc[dis, "P_batt"].to_numpy(), x, y)
            - np.interp(np.zeros(np.count_nonzero(dis)), x, y)
        )
        np.testing.assert_allclose(
            result.loc[dis, "issue355_candidate_loss_W"],
            expected_loss,
            atol=1e-5,
            rtol=0,
        )
        expected_hybrid = (
            0.983 * result.loc[dis, "P_batt"].to_numpy()
            - result.loc[dis, "issue355_extra_loss_W"].to_numpy()
        )
        np.testing.assert_allclose(
            result.loc[dis, "P_hybrid_inverter"],
            expected_hybrid,
            atol=1e-5,
            rtol=0,
        )

    def test_daylight_discharge_matches_incremental_pwl_definition(self):
        df, pv, load = scenario(
            pv=[500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000],
            load=[3500] * 10,
            buy=0.45,
            sell=0.0,
        )
        opt = build_opt(research=True, tolerance=30)
        result = solve(opt, df, pv, load, 0.7, 0.6)

        dis = result["P_batt"].to_numpy() > 1.0
        self.assertTrue(np.any(dis))
        x, y = opt._issue355_research_pwl_points()
        p = pv.to_numpy()[dis]
        b = result.loc[dis, "P_batt"].to_numpy()
        expected = np.interp(p + b, x, y) - np.interp(p, x, y)
        np.testing.assert_allclose(
            result.loc[dis, "issue355_candidate_loss_W"],
            expected,
            atol=1e-5,
            rtol=0,
        )

    def test_30w_representation_adds_two_segment_binaries_per_timestep(self):
        df, pv, load = scenario(pv=[0] * 10, load=[1000] * 10)
        opt = build_opt(research=True, tolerance=30)
        solve(opt, df, pv, load)
        self.assertEqual(opt.vars["issue355_pwl_segment_active"].shape, (2, 10))
        self.assertEqual(opt.vars["issue355_pwl_delta"].shape, (3, 10))

    def test_multi_battery_rejected_for_research_spike(self):
        with self.assertRaisesRegex(ValueError, "exactly one battery"):
            build_opt(
                research=True,
                plant_overrides={
                    "number_of_batteries": 2,
                    "battery_discharge_power_max": [5000, 5000],
                    "battery_charge_power_max": [5000, 5000],
                    "battery_minimum_state_of_charge": [0.1, 0.1],
                    "battery_maximum_state_of_charge": [0.9, 0.9],
                    "battery_target_state_of_charge": [0.5, 0.5],
                    "battery_nominal_energy_capacity": [10000, 10000],
                    "battery_discharge_efficiency": [0.9747, 0.9747],
                    "battery_charge_efficiency": [0.9747, 0.9747],
                    "battery_stress_cost": [0.0, 0.0],
                },
            )

    def test_invalid_representation_rejected(self):
        opt = build_opt(research=True, tolerance=17)
        with self.assertRaisesRegex(ValueError, "must be one of"):
            opt._issue355_research_pwl_points()


if __name__ == "__main__":
    unittest.main()
