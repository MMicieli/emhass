#!/usr/bin/env python
"""Contract tests for additive multi-component capacity charges (issue #150).

The existing scalar capacity-charge API remains the primary component and is
not retyped or replaced. ``capacity_charge_additional_components`` adds only
component 2..K. All components share the existing tariff measurement interval
and realised open-interval import history, while each has independent rate,
incumbent, eligibility and MPC-consideration state.

All scenarios are synthetic and tariff-provider agnostic.
"""

import logging
import pathlib
import unittest

import numpy as np
import pandas as pd

from emhass.optimization import Optimization

TEST_ROOT = pathlib.Path(__file__).resolve().parents[1]


def build_optimization(optim_overrides=None, plant_overrides=None) -> Optimization:
    logger = logging.getLogger("capacity_additional_components_test")
    logger.handlers = []
    logger.addHandler(logging.NullHandler())

    retrieve_hass_conf = {
        "optimization_time_step": pd.to_timedelta(5, "minutes"),
        "time_zone": "Europe/Tallinn",
        "sensor_power_photovoltaics": "pv",
        "sensor_power_load_no_var_loads": "load",
    }
    optim_conf = {
        "delta_forecast_daily": pd.Timedelta(hours=2),
        "num_threads": 0,
        "set_use_battery": False,
        "set_use_pv": True,
        "set_total_pv_sell": False,
        "set_nocharge_from_grid": False,
        "set_nodischarge_to_grid": True,
        "set_battery_dynamic": False,
        "set_battery_first_priority": False,
        "battery_dynamic_max": 0.9,
        "battery_dynamic_min": -0.9,
        "weight_battery_discharge": 0.1,
        "weight_battery_charge": 0.1,
        "battery_soc_deficit_threshold": 0.0,
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
        "capacity_cost_per_kw": 0.0,
        "capacity_charge_interval_timesteps": 1,
        "capacity_charge_additional_components": None,
    }
    if optim_overrides:
        optim_conf.update(optim_overrides)

    plant_conf = {
        "inverter_is_hybrid": False,
        "compute_curtailment": False,
        "maximum_power_from_grid": 50000,
        "maximum_power_to_grid": 50000,
        "battery_discharge_power_max": 20000,
        "battery_charge_power_max": 20000,
        "battery_minimum_state_of_charge": 0.0,
        "battery_maximum_state_of_charge": 1.0,
        "battery_target_state_of_charge": 0.5,
        "battery_nominal_energy_capacity": 10000,
        "battery_discharge_efficiency": 1.0,
        "battery_charge_efficiency": 1.0,
        "battery_stress_cost": 0.0,
        "battery_stress_segments": 10,
    }
    if plant_overrides:
        plant_conf.update(plant_overrides)

    return Optimization(
        retrieve_hass_conf,
        optim_conf,
        plant_conf,
        "unit_load_cost",
        "unit_prod_price",
        "profit",
        {"root_path": TEST_ROOT / "src" / "emhass", "data_path": TEST_ROOT / "data"},
        logger,
        opt_time_delta=5,
    )


def make_scenario(load, unit_load_cost=0.20, unit_prod_price=0.20):
    n = len(load)
    index = pd.date_range("2026-02-01", periods=n, freq="5min", tz="Europe/Tallinn")
    p_pv = pd.Series(np.zeros(n), index=index)
    p_load = pd.Series(load, index=index, dtype=float)
    df_input = pd.DataFrame(index=index)
    df_input["unit_load_cost"] = unit_load_cost
    df_input["unit_prod_price"] = unit_prod_price
    return p_pv, p_load, df_input


class TestCapacityAdditionalComponentsContract(unittest.TestCase):
    def test_absent_additional_components_keeps_legacy_k1_shape(self):
        opt = build_optimization(optim_overrides={"capacity_cost_per_kw": 2.0})
        self.assertNotIn("peak_import_additional", opt.vars)
        self.assertFalse(hasattr(opt, "param_capacity_window_additional"))
        self.assertFalse(hasattr(opt, "param_current_period_peak_additional"))

    def test_two_components_have_independent_windows_incumbents_and_rates(self):
        load = [1000.0, 2000.0, 1000.0, 1000.0, 3000.0, 1000.0]
        pv, load_s, df = make_scenario(load)
        primary_window = [1, 1, 1, 0, 0, 0]
        additional_window = [0, 0, 0, 1, 1, 1]

        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": 3.0,
                "capacity_charge_additional_components": [{"capacity_cost_per_kw": 7.0}],
            }
        )
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            len(load),
            current_period_peak=500.0,
            capacity_charge_window=primary_window,
            capacity_charge_additional_state=[
                {
                    "current_period_peak": 800.0,
                    "capacity_charge_window": additional_window,
                }
            ],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertAlmostEqual(opt.vars["peak_import"].value, 2000.0, places=3)
        self.assertEqual(len(opt.vars["peak_import_additional"]), 1)
        self.assertAlmostEqual(opt.vars["peak_import_additional"][0].value, 3000.0, places=3)
        np.testing.assert_array_equal(opt.param_capacity_window.value, primary_window)
        np.testing.assert_array_equal(opt.param_capacity_window_additional[0].value, additional_window)
        self.assertAlmostEqual(opt.param_current_period_peak.value, 500.0, places=6)
        self.assertAlmostEqual(opt.param_current_period_peak_additional[0].value, 800.0, places=6)

    def test_consideration_is_independent_per_component_and_resets(self):
        load = [0.0, 2000.0, 0.0, 0.0, 5000.0, 0.0]
        pv, load_s, df = make_scenario(load)
        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": 2.0,
                "capacity_charge_additional_components": [{"capacity_cost_per_kw": 2.0}],
            }
        )

        # Primary excludes its 2 kW event; additional component considers all.
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            len(load),
            capacity_charge_consideration=[1, 0, 1, 1, 1, 1],
            capacity_charge_additional_state=[{"capacity_charge_consideration": [1] * len(load)}],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertAlmostEqual(opt.vars["peak_import"].value, 5000.0, places=3)
        self.assertAlmostEqual(opt.vars["peak_import_additional"][0].value, 5000.0, places=3)

        # Keep only the primary event in its eligibility window and exclude the
        # additional component's 5 kW event. Values must not leak between them.
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            len(load),
            capacity_charge_window=[1, 1, 1, 0, 0, 0],
            capacity_charge_additional_state=[
                {
                    "capacity_charge_window": [0, 0, 0, 1, 1, 1],
                    "capacity_charge_consideration": [1, 1, 1, 1, 0, 1],
                }
            ],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertAlmostEqual(opt.vars["peak_import"].value, 2000.0, places=3)
        self.assertAlmostEqual(opt.vars["peak_import_additional"][0].value, 0.0, places=3)

        # Omission on the next solve restores full consideration/default window
        # for the additional component rather than leaking the prior masks.
        opt.perform_naive_mpc_optim(df, pv, load_s, len(load))
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertAlmostEqual(opt.vars["peak_import_additional"][0].value, 5000.0, places=3)

    def test_additional_consideration_never_erases_realised_incumbent(self):
        load = [0.0] * 6
        pv, load_s, df = make_scenario(load)
        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": 1.0,
                "capacity_charge_additional_components": [{"capacity_cost_per_kw": 1.0}],
            }
        )
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            6,
            capacity_charge_additional_state=[
                {
                    "current_period_peak": 3200.0,
                    "capacity_charge_consideration": [0] * 6,
                }
            ],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertAlmostEqual(opt.vars["peak_import_additional"][0].value, 3200.0, places=3)

    def test_n_gt_1_uses_shared_physical_open_interval_history(self):
        # N=4, two already-realised samples [500, 500]. The first completed
        # candidate ends at decision index 1 and is (500+500+2000+2000)/4=1250 W.
        load = [2000.0, 2000.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        pv, load_s, df = make_scenario(load)
        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": 1.0,
                "capacity_charge_interval_timesteps": 4,
                "capacity_charge_additional_components": [{"capacity_cost_per_kw": 1.0}],
            }
        )
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            len(load),
            capacity_charge_current_interval_history=[500.0, 500.0],
            capacity_charge_additional_state=[
                {
                    # Same physical realised history, but this component excludes
                    # the first interval prospectively at its endpoint.
                    "capacity_charge_consideration": [1, 0, 1, 1, 1, 1, 1, 1],
                }
            ],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertAlmostEqual(opt.vars["peak_import"].value, 1250.0, places=3)
        self.assertAlmostEqual(opt.vars["peak_import_additional"][0].value, 0.0, places=3)

    def test_runtime_updates_preserve_dpp_and_problem_identity(self):
        load = [1000.0, 2000.0, 1000.0, 1000.0, 3000.0, 1000.0]
        pv, load_s, df = make_scenario(load)
        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": 3.0,
                "capacity_charge_additional_components": [{"capacity_cost_per_kw": 7.0}],
            }
        )
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            len(load),
            capacity_charge_additional_state=[
                {
                    "current_period_peak": 200.0,
                    "capacity_charge_window": [0, 0, 0, 1, 1, 1],
                    "capacity_charge_consideration": [1] * len(load),
                }
            ],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertTrue(opt.prob.is_dpp())
        prob_id = id(opt.prob)

        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            len(load),
            capacity_charge_additional_state=[
                {
                    "current_period_peak": 900.0,
                    "capacity_charge_window": [0, 0, 1, 1, 1, 0],
                    "capacity_charge_consideration": [1, 1, 1, 1, 0, 1],
                }
            ],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertTrue(opt.prob.is_dpp())
        self.assertEqual(id(opt.prob), prob_id)

    def test_additional_components_require_active_primary_component(self):
        with self.assertLogs(level="WARNING") as logs:
            opt = build_optimization(
                optim_overrides={
                    "capacity_cost_per_kw": 0.0,
                    "capacity_charge_additional_components": [{"capacity_cost_per_kw": 3.0}],
                }
            )
        self.assertFalse(hasattr(opt, "param_capacity_window_additional"))
        self.assertTrue(
            any("capacity_charge_additional_components" in line for line in logs.output),
            msg=f"expected an explicit configuration warning, got: {logs.output}",
        )


if __name__ == "__main__":
    unittest.main()
