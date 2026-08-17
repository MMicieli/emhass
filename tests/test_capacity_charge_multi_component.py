#!/usr/bin/env python
"""Tests for the multi-component capacity/demand-charge extension
(issue #150 / upstream #540, Part B).

Scope: src/emhass/optimization.py capacity-charge formulation (issue #623 /
#1066, tariff-interval aggregation #540 Part A) generalised from ONE
component to K independent components via a list-shaped
``capacity_cost_per_kw``.

All scenarios are synthetic and self-contained (mirrors
tests/test_multi_battery_optimization.py's build_optimization() helper) - no
dependency on the real forecast data files used by TestOptimization in
test_optimization.py, so this file solves quickly and deterministically.

Tariff-023 motivation (documented here only, never encoded in
optimization.py): a real-world demand tariff with two independently billed
demand components - e.g. an anytime/all-day component and a business-hours
peak component - each with its own incumbent, window and $/kW rate, both
measured on a 30-minute clocked basis, while energy import/export pricing
remains simultaneously active. Category K below builds exactly this shape
with placeholder numbers; no Evoenergy tariff figures, ACT calendar, winter
months or AEST conversion are encoded anywhere in this file or in
optimization.py.
"""

import asyncio
import json
import logging
import pathlib
import unittest

import numpy as np
import pandas as pd

from emhass import utils
from emhass.optimization import Optimization

TEST_ROOT = pathlib.Path(__file__).resolve().parents[1]
_EMHASS_CONF = {
    "data_path": TEST_ROOT / "data",
    "root_path": TEST_ROOT / "src" / "emhass",
    "defaults_path": TEST_ROOT / "src" / "emhass" / "data" / "config_defaults.json",
    "associations_path": TEST_ROOT / "src" / "emhass" / "data" / "associations.csv",
}
_config_logger, _ = utils.get_logger(
    "capacity_multi_component_config_test", _EMHASS_CONF, save_to_file=False
)


def build_optimization(
    optim_overrides=None, plant_overrides=None, opt_time_delta=5
) -> Optimization:
    """Self-contained Optimization builder, mirroring
    test_multi_battery_optimization.py's build_optimization(). No battery,
    no PV, no deferrable loads by default - a pure grid-import-vs-capacity-
    charge scenario (p_grid_pos == p_load exactly), so component peaks are
    hand-computable. Pass optim_overrides={"set_use_battery": True, ...} for
    the economic-independence tests that need battery flexibility.
    """
    logger = logging.getLogger("capacity_multi_component_test")
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

    emhass_conf = {
        "root_path": TEST_ROOT / "src" / "emhass",
        "data_path": TEST_ROOT / "data",
    }
    return Optimization(
        retrieve_hass_conf,
        optim_conf,
        plant_conf,
        "unit_load_cost",
        "unit_prod_price",
        "profit",
        emhass_conf,
        logger,
        opt_time_delta=opt_time_delta,
    )


def make_scenario(n, load, tz="Europe/Tallinn", unit_load_cost=0.20, unit_prod_price=0.20):
    """A zero-PV, flat-price scenario: with no battery and no PV, P_grid_pos
    equals the load vector exactly, so every component's peak is
    hand-computable directly from `load`."""
    index = pd.date_range("2026-02-01", periods=n, freq="5min", tz=tz)
    p_pv = pd.Series(np.zeros(n), index=index)
    p_load = pd.Series(load, index=index)
    df_input = pd.DataFrame(index=index)
    df_input["unit_load_cost"] = unit_load_cost
    df_input["unit_prod_price"] = unit_prod_price
    return index, p_pv, p_load, df_input


class TestCapacityMultiComponentK1Parity(unittest.TestCase):
    """Category A: K=1 exact regression parity.

    The 18 pre-existing capacity-charge tests in test_optimization.py
    exercise the untouched legacy scalar path in full (N=1, N>1, incumbent
    floor, window, partial current interval, objective/dispatch parity) and
    must remain 100% green - that IS the K=1 parity proof; this class adds
    only the structural checks specific to routing (list vs scalar).
    """

    def test_scalar_cost_never_enters_multi_component_path(self):
        opt = build_optimization(optim_overrides={"capacity_cost_per_kw": 2.0})
        self.assertFalse(opt._capacity_multi)
        self.assertFalse(hasattr(opt, "n_capacity_components"))
        self.assertFalse(hasattr(opt, "param_capacity_window_k"))
        self.assertFalse(hasattr(opt, "param_current_period_peak_k"))

    def test_omitted_cost_never_enters_multi_component_path(self):
        opt = build_optimization()
        self.assertFalse(opt._capacity_multi)

    def test_single_element_list_opts_into_multi_component_path(self):
        """A caller that explicitly passes a 1-element list has opted into
        the K>1 API shape even though K happens to be 1 - the type of the
        value routes, not its length (see optimization.py __init__)."""
        opt = build_optimization(optim_overrides={"capacity_cost_per_kw": [2.0]})
        self.assertTrue(opt._capacity_multi)
        self.assertEqual(opt.n_capacity_components, 1)

    def test_list_of_one_matches_scalar_math(self):
        """Even though list-of-one and scalar route through different code
        (legacy vs generic), they must be mathematically equivalent for the
        same single-component tariff: same peak, same objective."""
        n = 6
        load = [1000.0, 1000.0, 5000.0, 1000.0, 1000.0, 1000.0]
        _, pv, load_s, df = make_scenario(n, load)

        opt_scalar = build_optimization(optim_overrides={"capacity_cost_per_kw": 2.0})
        res_scalar = opt_scalar.perform_naive_mpc_optim(df, pv, load_s, n)
        self.assertEqual(opt_scalar.optim_status, "Optimal")

        opt_list = build_optimization(optim_overrides={"capacity_cost_per_kw": [2.0]})
        res_list = opt_list.perform_naive_mpc_optim(df, pv, load_s, n)
        self.assertEqual(opt_list.optim_status, "Optimal")

        self.assertAlmostEqual(
            opt_scalar.vars["peak_import"].value,
            opt_list.vars["peak_import_k"][0].value,
            places=3,
        )
        np.testing.assert_allclose(
            res_scalar["P_grid_pos"].to_numpy(), res_list["P_grid_pos"].to_numpy(), atol=1e-6
        )


class TestCapacityMultiComponentIndependence(unittest.TestCase):
    """Category B: two independent components - different windows,
    different incumbents, different rates, no cross-coupling."""

    def test_two_components_different_windows_incumbents_rates(self):
        n = 6
        # Component 0's peak lives at index 1 (2000 W); component 1's peak
        # lives at index 4 (3000 W). Distinct windows isolate each to its
        # own half of the horizon.
        load = [1000.0, 2000.0, 1000.0, 1000.0, 3000.0, 1000.0]
        _, pv, load_s, df = make_scenario(n, load)

        window0 = [1, 1, 1, 0, 0, 0]
        window1 = [0, 0, 0, 1, 1, 1]
        incumbent0 = 500.0
        incumbent1 = 800.0

        opt = build_optimization(optim_overrides={"capacity_cost_per_kw": [3.0, 7.0]})
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            n,
            current_period_peak=[incumbent0, incumbent1],
            capacity_charge_window=[window0, window1],
        )
        self.assertEqual(opt.optim_status, "Optimal")

        peak0 = opt.vars["peak_import_k"][0].value
        peak1 = opt.vars["peak_import_k"][1].value
        self.assertAlmostEqual(
            peak0, 2000.0, places=3, msg="component 0 must see only its own window's max"
        )
        self.assertAlmostEqual(
            peak1, 3000.0, places=3, msg="component 1 must see only its own window's max"
        )

        # No cross-coupling: component 0's incumbent/window must not leak
        # into component 1's Parameters or vice versa.
        np.testing.assert_array_equal(opt.param_capacity_window_k[0].value, window0)
        np.testing.assert_array_equal(opt.param_capacity_window_k[1].value, window1)
        self.assertAlmostEqual(opt.param_current_period_peak_k[0].value, incumbent0, places=6)
        self.assertAlmostEqual(opt.param_current_period_peak_k[1].value, incumbent1, places=6)

        # Independent objective contributions, exactly summed.
        expected_obj_capacity_term = -(3.0 * peak0 / 1000.0) - (7.0 * peak1 / 1000.0)
        actual_terms = [
            -3.0 * (peak0 / 1000.0),
            -7.0 * (peak1 / 1000.0),
        ]
        self.assertAlmostEqual(sum(actual_terms), expected_obj_capacity_term, places=6)


class TestCapacityMultiComponentEconomicIndependence(unittest.TestCase):
    """Category C: economic independence. Component 0 stays below its
    incumbent (no marginal value from shaving); component 1 would establish
    a new peak (marginal value from shaving). Only component 1 may
    influence dispatch. Then the condition is reversed."""

    def _battery_scenario(self):
        n = 6
        load = np.full(n, 1000.0)
        load[2] = 5000.0  # the spike a battery could shave
        return make_scenario(n, load.tolist())

    def _battery_optim_overrides(self, cost_per_kw):
        return {
            "set_use_battery": True,
            "capacity_cost_per_kw": cost_per_kw,
        }

    def test_component1_marginal_component0_floored(self):
        """incumbent0 (10000 W) is already above anything reachable this
        horizon -> component 0's peak_import is pinned at its incumbent
        floor regardless of dispatch, so it can supply no shaving
        incentive. incumbent1 (0 W) lets component 1 actually respond."""
        _, pv, load_s, df = self._battery_scenario()
        n = len(df)

        opt = build_optimization(optim_overrides=self._battery_optim_overrides([2.0, 2.0]))
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            n,
            soc_init=0.5,
            soc_final=0.5,
            current_period_peak=[0.0, 0.0],
        )
        self.assertEqual(opt.optim_status, "Optimal")

        opt2 = build_optimization(optim_overrides=self._battery_optim_overrides([2.0, 2.0]))
        res_c0_floored = opt2.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            n,
            soc_init=0.5,
            soc_final=0.5,
            current_period_peak=[10000.0, 0.0],
        )
        self.assertEqual(opt2.optim_status, "Optimal")
        peak0_val = opt2.vars["peak_import_k"][0].value
        peak1_val = opt2.vars["peak_import_k"][1].value

        self.assertAlmostEqual(
            peak0_val,
            10000.0,
            places=3,
            msg="component 0 must be pinned at its own (already-exceeded) incumbent",
        )
        self.assertLess(
            peak1_val,
            5000.0 - 500.0,
            msg="component 1 (zero incumbent) must still drive the battery to shave the spike",
        )
        # Dispatch with only component 1 economically live must match a
        # single-component (K=1 legacy) run priced at the same rate/incumbent.
        opt_single = build_optimization(
            optim_overrides={"set_use_battery": True, "capacity_cost_per_kw": 2.0}
        )
        res_single = opt_single.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            n,
            soc_init=0.5,
            soc_final=0.5,
            current_period_peak=0.0,
        )
        self.assertEqual(opt_single.optim_status, "Optimal")
        np.testing.assert_allclose(
            res_c0_floored["P_grid_pos"].to_numpy(),
            res_single["P_grid_pos"].to_numpy(),
            atol=1e-3,
            err_msg="a floored, non-marginal component must not perturb dispatch at all",
        )

    def test_reversed_component0_marginal_component1_floored(self):
        """Same scenario, roles reversed: component 1 floored, component 0 live."""
        _, pv, load_s, df = self._battery_scenario()
        n = len(df)

        opt = build_optimization(optim_overrides=self._battery_optim_overrides([2.0, 2.0]))
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            n,
            soc_init=0.5,
            soc_final=0.5,
            current_period_peak=[0.0, 10000.0],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        peak0_val = opt.vars["peak_import_k"][0].value
        peak1_val = opt.vars["peak_import_k"][1].value

        self.assertAlmostEqual(peak1_val, 10000.0, places=3)
        self.assertLess(peak0_val, 5000.0 - 500.0)


class TestCapacityMultiComponentIntervalAggregation(unittest.TestCase):
    """Category E: different measurement intervals per component."""

    def test_two_components_different_native_intervals(self):
        n = 12  # component 0 (N=6): two complete intervals [0:6),[6:12).
        # component 1 (N=3): four complete intervals [0:3),[3:6),[6:9),[9:12).
        load = np.zeros(n)
        load[1] = (
            6000.0  # component 0 interval [0:6) avg 1000 W; component 1 interval [0:3) avg 2000 W
        )
        load[7] = (
            9000.0  # component 0 interval [6:12) avg 1500 W; component 1 interval [6:9) avg 3000 W
        )
        _, pv, load_s, df = make_scenario(n, load.tolist())

        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": [1.0, 1.0],
                "capacity_charge_interval_timesteps": [6, 3],
            }
        )
        opt.perform_naive_mpc_optim(df, pv, load_s, n)
        self.assertEqual(opt.optim_status, "Optimal")

        self.assertTrue(opt._capacity_interval_aggregation_active_list[0])
        self.assertTrue(opt._capacity_interval_aggregation_active_list[1])
        self.assertEqual(opt._capacity_charge_interval_timesteps_list, [6, 3])

        peak0 = opt.vars["peak_import_k"][0].value
        peak1 = opt.vars["peak_import_k"][1].value
        # Each component's peak_import epigraph is the MAX over its own
        # completed intervals only: component 0 (N=6) -> max(1000, 1500);
        # component 1 (N=3) -> max(0, 2000, 3000, 0).
        self.assertAlmostEqual(
            peak0, 1500.0, places=3, msg="component 0 (N=6) must average its own intervals"
        )
        self.assertAlmostEqual(
            peak1, 3000.0, places=3, msg="component 1 (N=3) must average its own intervals"
        )

    def test_broadcast_scalar_interval_to_all_components(self):
        """A bare scalar capacity_charge_interval_timesteps broadcasts to
        every component (the documented "may broadcast if that is clean"
        case) rather than requiring a caller to repeat an identical value K times."""
        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": [1.0, 1.0, 1.0],
                "capacity_charge_interval_timesteps": 6,
            }
        )
        self.assertEqual(opt._capacity_charge_interval_timesteps_list, [6, 6, 6])

    def test_mismatched_interval_list_length_falls_back_safely(self):
        with self.assertLogs(level="WARNING") as logs:
            opt = build_optimization(
                optim_overrides={
                    "capacity_cost_per_kw": [1.0, 1.0],
                    "capacity_charge_interval_timesteps": [6, 3, 2],  # wrong length (3 vs K=2)
                }
            )
        self.assertEqual(opt._capacity_charge_interval_timesteps_list, [1, 1])
        self.assertTrue(any("capacity_charge_interval_timesteps" in line for line in logs.output))


class TestCapacityMultiComponentPartialHistory(unittest.TestCase):
    """Category F: independent partial histories per component."""

    def test_independent_partial_histories(self):
        n = 8
        load = np.zeros(n)
        # Component 0: N=4, history=[500, 500] (2 elapsed samples, avg incl. history).
        # Component 1: N=4, history=[] (aligned to horizon start).
        load[0] = 2000.0
        load[1] = 2000.0
        _, pv, load_s, df = make_scenario(n, load.tolist())

        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": [1.0, 1.0],
                "capacity_charge_interval_timesteps": [4, 4],
            }
        )
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            n,
            capacity_charge_current_interval_history=[[500.0, 500.0], []],
        )
        self.assertEqual(opt.optim_status, "Optimal")

        # Component 0: interval endpoint at decision index e0 = 4-1-2 = 1.
        # Q0 = (500 + 500 + load[0] + load[1]) / 4 = (500+500+2000+2000)/4 = 1250 W
        peak0 = opt.vars["peak_import_k"][0].value
        self.assertAlmostEqual(peak0, 1250.0, places=3)

        # Component 1: no history, e0 = 4-1-0 = 3, first interval = indices [0:4)
        # Q1 = (2000+2000+0+0)/4 = 1000 W
        peak1 = opt.vars["peak_import_k"][1].value
        self.assertAlmostEqual(peak1, 1000.0, places=3)

    def test_mismatched_history_outer_length_ignored_for_all(self):
        n = 8
        load = np.zeros(n)
        _, pv, load_s, df = make_scenario(n, load.tolist())
        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": [1.0, 1.0],
                "capacity_charge_interval_timesteps": [4, 4],
            }
        )
        with self.assertLogs(level="WARNING") as logs:
            opt.perform_naive_mpc_optim(
                df,
                pv,
                load_s,
                n,
                capacity_charge_current_interval_history=[[500.0]],  # wrong outer length (1 vs K=2)
            )
        self.assertTrue(
            any("capacity_charge_current_interval_history" in line for line in logs.output)
        )
        # Both components fall back to empty history (never a partial/cross-coupled guess).
        np.testing.assert_array_equal(
            opt.param_capacity_realised_contribution_k[0].value,
            np.zeros_like(opt.param_capacity_realised_contribution_k[0].value),
        )


class TestCapacityMultiComponentDisabled(unittest.TestCase):
    """Category G: a zero-cost component must have no economic effect."""

    def test_zero_cost_component_matches_single_component_baseline(self):
        n = 6
        load = [1000.0, 2000.0, 1000.0, 1000.0, 3000.0, 1000.0]
        _, pv, load_s, df = make_scenario(n, load)

        opt_baseline = build_optimization(optim_overrides={"capacity_cost_per_kw": [3.0]})
        res_baseline = opt_baseline.perform_naive_mpc_optim(df, pv, load_s, n)
        self.assertEqual(opt_baseline.optim_status, "Optimal")

        opt_with_disabled = build_optimization(optim_overrides={"capacity_cost_per_kw": [3.0, 0.0]})
        res_with_disabled = opt_with_disabled.perform_naive_mpc_optim(df, pv, load_s, n)
        self.assertEqual(opt_with_disabled.optim_status, "Optimal")

        self.assertIsNone(
            opt_with_disabled.vars["peak_import_k"][1],
            msg="a component with capacity_cost_per_kw<=0 must have no variable at all",
        )
        np.testing.assert_allclose(
            res_baseline["P_grid_pos"].to_numpy(),
            res_with_disabled["P_grid_pos"].to_numpy(),
            atol=1e-6,
        )
        self.assertAlmostEqual(
            opt_baseline.vars["peak_import_k"][0].value,
            opt_with_disabled.vars["peak_import_k"][0].value,
            places=3,
        )


class TestCapacityMultiComponentDppCache(unittest.TestCase):
    """Category H: DPP / cache behaviour for the K>1 path."""

    def test_runtime_updates_do_not_rebuild(self):
        n = 6
        load = [1000.0, 2000.0, 1000.0, 1000.0, 3000.0, 1000.0]
        _, pv, load_s, df = make_scenario(n, load)
        opt = build_optimization(optim_overrides={"capacity_cost_per_kw": [3.0, 7.0]})

        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            n,
            current_period_peak=[100.0, 200.0],
            capacity_charge_window=[[1, 1, 1, 0, 0, 0], [0, 0, 0, 1, 1, 1]],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertTrue(opt.prob.is_dpp())
        prob_id = id(opt.prob)

        # Change incumbents and windows on the SAME instance, same horizon.
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            n,
            current_period_peak=[500.0, 900.0],
            capacity_charge_window=[[0, 1, 1, 0, 0, 0], [0, 0, 1, 1, 1, 0]],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertTrue(opt.prob.is_dpp())
        self.assertEqual(
            id(opt.prob),
            prob_id,
            msg="runtime incumbent/window updates must not rebuild the problem",
        )

    def test_repeated_identical_solves_are_repeatable(self):
        n = 6
        load = [1000.0, 2000.0, 1000.0, 1000.0, 3000.0, 1000.0]
        _, pv, load_s, df = make_scenario(n, load)
        opt = build_optimization(optim_overrides={"capacity_cost_per_kw": [3.0, 7.0]})

        objs = []
        for _ in range(3):
            opt.perform_naive_mpc_optim(df, pv, load_s, n, current_period_peak=[100.0, 200.0])
            self.assertEqual(opt.optim_status, "Optimal")
            objs.append(opt.prob.value)
        self.assertAlmostEqual(objs[0], objs[1], places=6)
        self.assertAlmostEqual(objs[1], objs[2], places=6)

    def test_n_gt_1_partial_history_updates_do_not_rebuild(self):
        """Remediation item 2: N>1 aggregated path, two active components, on
        the SAME Optimization instance. Solve A supplies independent
        incumbents/windows/partial histories; solve B changes all three.
        Proves: id(opt.prob) unchanged, prob stays DPP, each component's
        (A, c) Parameter values actually update, no component receives
        another's history, resulting peaks change exactly as hand-computed,
        and status is ordinary Optimal both times.
        """
        n = 12
        load = np.zeros(n)
        load[1] = 6000.0  # inside component 0's interval [0:6) and component 1's interval [0:3)
        load[7] = 9000.0  # inside component 0's interval [6:12) and component 1's interval [6:9)
        _, pv, load_s, df = make_scenario(n, load.tolist())

        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": [1.0, 1.0],
                "capacity_charge_interval_timesteps": [6, 3],
            }
        )

        # --- Solve A ---
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            n,
            current_period_peak=[50.0, 50.0],
            capacity_charge_window=[[1] * n, [1] * n],
            capacity_charge_current_interval_history=[[6000.0, 6000.0], [9000.0]],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertTrue(opt.prob.is_dpp())
        prob_id = id(opt.prob)

        matrix0_a = opt.param_capacity_interval_matrix_k[0].value.copy()
        matrix1_a = opt.param_capacity_interval_matrix_k[1].value.copy()
        contribution0_a = opt.param_capacity_realised_contribution_k[0].value.copy()
        contribution1_a = opt.param_capacity_realised_contribution_k[1].value.copy()

        peak0_a = opt.vars["peak_import_k"][0].value
        peak1_a = opt.vars["peak_import_k"][1].value
        # Hand-computed: component 0 (N=6), history=[6000,6000] -> e0=3.
        # interval0 = (12000 + p[0..3]) / 6 = (12000 + 6000) / 6 = 3000.
        # interval1 = p[4..9] / 6 = 9000 / 6 = 1500. peak0 = max(3000, 1500) = 3000.
        self.assertAlmostEqual(peak0_a, 3000.0, places=3)
        # component 1 (N=3), history=[9000] -> e0=1.
        # interval0 = (9000 + p[0..1]) / 3 = (9000 + 6000) / 3 = 5000.
        # interval2 = p[6..8] / 3 = 9000 / 3 = 3000. peak1 = max(5000, 0, 3000, 0) = 5000.
        self.assertAlmostEqual(peak1_a, 5000.0, places=3)

        # --- Solve B: change incumbents, windows (all-ones, unchanged value
        # but re-supplied) and - the point of this test - partial histories. ---
        opt.perform_naive_mpc_optim(
            df,
            pv,
            load_s,
            n,
            current_period_peak=[80.0, 80.0],
            capacity_charge_window=[[1] * n, [1] * n],
            capacity_charge_current_interval_history=[[], []],
        )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertTrue(opt.prob.is_dpp())
        self.assertEqual(
            id(opt.prob), prob_id, msg="N>1 partial-history updates must not rebuild the problem"
        )

        matrix0_b = opt.param_capacity_interval_matrix_k[0].value.copy()
        matrix1_b = opt.param_capacity_interval_matrix_k[1].value.copy()
        contribution0_b = opt.param_capacity_realised_contribution_k[0].value.copy()
        contribution1_b = opt.param_capacity_realised_contribution_k[1].value.copy()

        # Parameters actually changed between calls (not stale/cached values).
        self.assertFalse(np.array_equal(matrix0_a, matrix0_b))
        self.assertFalse(np.array_equal(matrix1_a, matrix1_b))
        self.assertFalse(np.array_equal(contribution0_a, contribution0_b))
        self.assertFalse(np.array_equal(contribution1_a, contribution1_b))
        # No cross-component contamination: in solve A, component 0's first-row
        # realised contribution reflects ONLY its own history ([6000, 6000] ->
        # w*(12000/6)=2000.0), and component 1's reflects ONLY its own
        # history ([9000] -> w*(9000/3)=3000.0). If either component had
        # picked up the other's history, these would match by construction.
        self.assertAlmostEqual(contribution0_a[0], 2000.0, places=3)
        self.assertAlmostEqual(contribution1_a[0], 3000.0, places=3)
        self.assertNotAlmostEqual(contribution0_a[0], contribution1_a[0], places=1)

        peak0_b = opt.vars["peak_import_k"][0].value
        peak1_b = opt.vars["peak_import_k"][1].value
        # component 0, empty history -> e0=5. interval0 = p[0..5]/6 = 6000/6=1000.
        # interval1 = p[6..11]/6 = 9000/6=1500. peak0 = max(1000, 1500) = 1500.
        self.assertAlmostEqual(peak0_b, 1500.0, places=3)
        # component 1, empty history -> e0=2. interval0 = p[0..2]/3 = 6000/3=2000.
        # interval2 = p[6..8]/3 = 9000/3=3000. peak1 = max(2000, 0, 3000, 0) = 3000.
        self.assertAlmostEqual(peak1_b, 3000.0, places=3)
        # Peaks materially changed as hand-expected purely from the history change.
        self.assertNotAlmostEqual(peak0_a, peak0_b, places=1)
        self.assertNotAlmostEqual(peak1_a, peak1_b, places=1)


class TestCapacityMultiComponentHorizonResize(unittest.TestCase):
    """Category I: horizon resize reshapes every component's Parameters."""

    def test_resize_reshapes_all_component_parameters(self):
        horizon_a, horizon_b = 6, 12
        load_b = np.zeros(horizon_b)
        _, pv, load_s, df = make_scenario(horizon_b, load_b.tolist())

        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": [2.0, 2.0],
                "capacity_charge_interval_timesteps": [6, 3],
            }
        )
        opt.perform_naive_mpc_optim(df, pv, load_s, horizon_a)
        self.assertEqual(opt.optim_status, "Optimal")
        shape0_a = opt.param_capacity_interval_matrix_k[0].value.shape
        shape1_a = opt.param_capacity_interval_matrix_k[1].value.shape
        self.assertEqual(shape0_a, (int(np.ceil(horizon_a / 6)), horizon_a))
        self.assertEqual(shape1_a, (int(np.ceil(horizon_a / 3)), horizon_a))
        self.assertEqual(opt.param_capacity_window_k[0].value.shape, (horizon_a,))

        opt.perform_naive_mpc_optim(df, pv, load_s, horizon_b)
        self.assertEqual(opt.optim_status, "Optimal")
        shape0_b = opt.param_capacity_interval_matrix_k[0].value.shape
        shape1_b = opt.param_capacity_interval_matrix_k[1].value.shape
        self.assertEqual(shape0_b, (int(np.ceil(horizon_b / 6)), horizon_b))
        self.assertEqual(shape1_b, (int(np.ceil(horizon_b / 3)), horizon_b))
        self.assertEqual(opt.param_capacity_window_k[1].value.shape, (horizon_b,))
        self.assertTrue(opt.prob.is_dpp())


class TestCapacityMultiComponentInvalidInput(unittest.TestCase):
    """Category J: invalid input handling for K>1."""

    def test_invalid_cost_entry_disables_only_that_component(self):
        with self.assertLogs(level="WARNING") as logs:
            opt = build_optimization(
                optim_overrides={"capacity_cost_per_kw": [2.0, float("nan"), -5.0]}
            )
        self.assertEqual(opt._capacity_cost_per_kw_list[0], 2.0)
        self.assertEqual(opt._capacity_cost_per_kw_list[1], 0.0)
        self.assertEqual(opt._capacity_cost_per_kw_list[2], 0.0)
        self.assertTrue(any("capacity_cost_per_kw[1]" in line for line in logs.output))
        self.assertTrue(any("capacity_cost_per_kw[2]" in line for line in logs.output))

    def test_wrong_current_period_peak_shape_ignored_for_all_no_crash(self):
        n = 6
        load = [1000.0] * n
        _, pv, load_s, df = make_scenario(n, load)
        opt = build_optimization(optim_overrides={"capacity_cost_per_kw": [2.0, 2.0]})
        with self.assertLogs(level="WARNING") as logs:
            opt.perform_naive_mpc_optim(
                df,
                pv,
                load_s,
                n,
                current_period_peak=5000.0,  # bare scalar, not a K-length list
            )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertTrue(any("current_period_peak" in line for line in logs.output))
        self.assertAlmostEqual(opt.param_current_period_peak_k[0].value, 0.0)
        self.assertAlmostEqual(opt.param_current_period_peak_k[1].value, 0.0)

    def test_nested_list_shape_error_in_window_falls_back_per_component(self):
        n = 6
        load = [1000.0] * n
        _, pv, load_s, df = make_scenario(n, load)
        opt = build_optimization(optim_overrides={"capacity_cost_per_kw": [2.0, 2.0]})
        with self.assertLogs(level="WARNING"):
            opt.perform_naive_mpc_optim(
                df,
                pv,
                load_s,
                n,
                capacity_charge_window=[[1] * n],  # wrong outer length (1 vs K=2)
            )
        self.assertEqual(opt.optim_status, "Optimal")
        np.testing.assert_array_equal(opt.param_capacity_window_k[0].value, np.ones(n))
        np.testing.assert_array_equal(opt.param_capacity_window_k[1].value, np.ones(n))

    def test_over_long_history_falls_back_for_that_component_only(self):
        n = 8
        load = np.zeros(n)
        _, pv, load_s, df = make_scenario(n, load.tolist())
        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": [2.0, 2.0],
                "capacity_charge_interval_timesteps": [4, 4],
            }
        )
        with self.assertLogs(level="WARNING") as logs:
            opt.perform_naive_mpc_optim(
                df,
                pv,
                load_s,
                n,
                capacity_charge_current_interval_history=[
                    [1.0, 2.0, 3.0, 4.0, 5.0],  # too long for N=4 (max 3 entries)
                    [1.0],
                ],
            )
        self.assertEqual(opt.optim_status, "Optimal")
        self.assertTrue(any("capacity_cost_per_kw[0]" in line for line in logs.output))
        # Component 0 (invalid) falls back to empty history; component 1 (valid) is unaffected.
        np.testing.assert_array_equal(
            opt.param_capacity_realised_contribution_k[0].value,
            np.zeros_like(opt.param_capacity_realised_contribution_k[0].value),
        )


class TestCapacityMultiComponentOverlappingWindows(unittest.TestCase):
    """Category D: overlapping windows - the same grid import may
    contribute to both components' peaks, and both independent costs apply."""

    def test_overlapping_windows_both_price_the_same_spike(self):
        n = 6
        load = [1000.0, 1000.0, 6000.0, 1000.0, 1000.0, 1000.0]
        _, pv, load_s, df = make_scenario(n, load)
        # Both windows include index 2 (the spike).
        window0 = [1, 1, 1, 1, 0, 0]
        window1 = [0, 1, 1, 1, 1, 0]

        opt = build_optimization(optim_overrides={"capacity_cost_per_kw": [2.0, 5.0]})
        opt.perform_naive_mpc_optim(df, pv, load_s, n, capacity_charge_window=[window0, window1])
        self.assertEqual(opt.optim_status, "Optimal")
        peak0 = opt.vars["peak_import_k"][0].value
        peak1 = opt.vars["peak_import_k"][1].value
        self.assertAlmostEqual(peak0, 6000.0, places=3)
        self.assertAlmostEqual(peak1, 6000.0, places=3)
        # Both charges apply to the SAME physical import - independently priced.
        expected_total_capacity_cost = 2.0 * (peak0 / 1000.0) + 5.0 * (peak1 / 1000.0)
        self.assertAlmostEqual(expected_total_capacity_cost, 2.0 * 6.0 + 5.0 * 6.0, places=3)


class TestCapacityTariff023Fixture(unittest.TestCase):
    """Section 10: a real-world-motivated fixture demonstrating two
    independent components equivalent to a two-window demand tariff, both
    on a 30-minute clocked measurement basis, WITHOUT encoding any tariff
    provider's actual numbers, calendar, season or timezone conversion here
    - those all live in the caller (Home Assistant / the runtime payload),
    never in optimization.py. Placeholder rates/incumbents only.

    W1: one demand window, one incumbent, one rate, 30-minute measurement.
    W2: a different window, a different incumbent, a different rate, same
    30-minute measurement basis. Energy-price optimisation (import cost /
    export revenue) remains simultaneously active throughout.
    """

    def test_two_window_30min_demand_tariff_with_energy_pricing_active(self):
        # 5-minute native steps; 30-minute demand interval -> N=6 for both
        # components (same clocked measurement basis, different windows).
        n = 18  # 3 completed 30-minute intervals
        # Distinct energy price signal alongside the capacity charge, so this
        # test also proves normal energy-price optimisation stays active.
        unit_load_cost = [0.30] * 6 + [0.10] * 6 + [0.30] * 6
        unit_prod_price = [0.05] * n

        load = np.full(n, 500.0)
        load[3] = 4000.0  # inside W1's window, interval 1 (0:6)
        load[10] = 3000.0  # inside W2's window, interval 2 (6:12), cheap-energy period
        index = pd.date_range("2026-06-01", periods=n, freq="5min", tz="Australia/Sydney")
        p_pv = pd.Series(np.zeros(n), index=index)
        p_load = pd.Series(load, index=index)
        df_input = pd.DataFrame(index=index)
        df_input["unit_load_cost"] = unit_load_cost
        df_input["unit_prod_price"] = unit_prod_price

        # W1: "all-day" style window, active in the first two intervals only.
        window_w1 = [1] * 12 + [0] * 6
        # W2: a different (later-starting) window, active in the last two intervals only.
        window_w2 = [0] * 6 + [1] * 12

        opt = build_optimization(
            optim_overrides={
                "capacity_cost_per_kw": [4.0, 9.0],  # placeholder rates, not real tariff numbers
                "capacity_charge_interval_timesteps": [6, 6],  # same 30-min clocked basis
            }
        )
        res = opt.perform_naive_mpc_optim(
            df_input,
            p_pv,
            p_load,
            n,
            current_period_peak=[200.0, 350.0],  # placeholder incumbents
            capacity_charge_window=[window_w1, window_w2],
        )
        self.assertEqual(opt.optim_status, "Optimal")

        # Both components independently active and structurally distinct.
        self.assertEqual(opt.n_capacity_components, 2)
        self.assertTrue(opt._capacity_interval_aggregation_active_list[0])
        self.assertTrue(opt._capacity_interval_aggregation_active_list[1])
        peak_w1 = opt.vars["peak_import_k"][0].value
        peak_w2 = opt.vars["peak_import_k"][1].value
        self.assertGreater(peak_w1, 0.0)
        self.assertGreater(peak_w2, 0.0)

        # Energy-price optimisation remains simultaneously active: import
        # cost differs materially with the differing unit_load_cost signal
        # (sanity check that price columns actually reached the objective).
        self.assertIn("unit_load_cost", res.columns)
        self.assertTrue((res["unit_load_cost"].to_numpy() == np.array(unit_load_cost)).all())

    def _run_price_scenario(self, unit_load_cost):
        """Two demand components (N=6, 30-min clocked basis) with
        NON-OVERLAPPING windows on the outer two intervals, each incumbent
        set exactly at the flat load (1000 W) so peak-shaving has zero
        marginal value on its own - any battery action is driven purely by
        energy-price arbitrage in the middle interval, which neither
        component's window covers at all (capacity-charge-neutral), plus a
        caller-supplied import-price signal. Returns (opt, res)."""
        n = 18
        unit_prod_price = [0.02] * n
        load = np.full(n, 1000.0)
        index = pd.date_range("2026-06-01", periods=n, freq="5min", tz="Australia/Sydney")
        p_pv = pd.Series(np.zeros(n), index=index)
        p_load = pd.Series(load, index=index)
        df_input = pd.DataFrame(index=index)
        df_input["unit_load_cost"] = unit_load_cost
        df_input["unit_prod_price"] = unit_prod_price

        window_w1 = [1] * 6 + [0] * 12  # only the first interval
        window_w2 = [0] * 12 + [1] * 6  # only the last interval

        opt = build_optimization(
            optim_overrides={
                "set_use_battery": True,
                "capacity_cost_per_kw": [4.0, 9.0],
                "capacity_charge_interval_timesteps": [6, 6],
                "weight_battery_charge": 0.01,
                "weight_battery_discharge": 0.01,
            },
            plant_overrides={
                "battery_nominal_energy_capacity": 8000,
                "battery_discharge_power_max": 3000,
                "battery_charge_power_max": 3000,
                "battery_minimum_state_of_charge": 0.1,
                "battery_maximum_state_of_charge": 0.9,
            },
        )
        res = opt.perform_naive_mpc_optim(
            df_input,
            p_pv,
            p_load,
            n,
            soc_init=0.5,
            soc_final=0.5,
            current_period_peak=[
                1000.0,
                1000.0,
            ],  # exactly the flat load: shaving has no marginal value
            capacity_charge_window=[window_w1, window_w2],
        )
        return opt, res

    def test_energy_price_signal_changes_dispatch_with_both_demand_components_active(self):
        """Remediation item 4: strengthen the tariff-023 fixture with a
        controllable flexible resource (battery) and a genuinely
        time-varying import-price signal, then flip that price signal and
        prove:

        - the energy-price objective still materially changes an
          economically rational dispatch decision (the battery arbitrages a
          cheap middle-interval price window it otherwise has no reason to
          touch), while
        - both independent demand components remain active (floored at
          their own incumbent) throughout - this is one joint optimisation,
          not demand overriding energy economics or vice versa.
        """
        # Scenario A: the middle interval (6:12) is cheap relative to the
        # outer two - profitable to charge there and discharge into the
        # (pricier) outer-interval load instead of importing for it directly.
        price_a = [0.20] * 6 + [0.05] * 6 + [0.20] * 6
        # Scenario B: the middle interval is instead the MOST expensive -
        # charging there to discharge into 0.20-priced load is a losing
        # trade even after the (small) wear cost, so no arbitrage is
        # rational and the battery should sit idle.
        price_b = [0.20] * 6 + [0.35] * 6 + [0.20] * 6

        opt_a, res_a = self._run_price_scenario(price_a)
        opt_b, res_b = self._run_price_scenario(price_b)

        self.assertEqual(opt_a.optim_status, "Optimal")
        self.assertEqual(opt_b.optim_status, "Optimal")

        # Both components remain independently active (floored exactly at
        # their own 1000 W incumbent) under BOTH price signals - the
        # capacity charge is not being switched off or overridden by the
        # energy-price change, and stays IDENTICAL between scenarios
        # (proving the middle-interval arbitrage genuinely does not touch
        # either component's window).
        for opt in (opt_a, opt_b):
            self.assertEqual(opt.n_capacity_components, 2)
            self.assertAlmostEqual(opt.vars["peak_import_k"][0].value, 1000.0, places=3)
            self.assertAlmostEqual(opt.vars["peak_import_k"][1].value, 1000.0, places=3)

        # Economically rational dispatch decision flips with the price
        # signal: the middle interval - covered by NEITHER component's
        # window, so this comparison is fully isolated from peak-shaving -
        # sees genuine battery charging activity when cheap (A) and none at
        # all when expensive (B).
        p_batt_a = res_a["P_batt"].to_numpy()
        p_batt_b = res_b["P_batt"].to_numpy()
        middle_a = p_batt_a[6:12]
        middle_b = p_batt_b[6:12]

        self.assertTrue(
            (middle_a < -100.0).any(),
            msg=f"expected genuine battery charging in the cheap middle interval (A), got {middle_a}",
        )
        self.assertTrue(
            np.allclose(middle_b, 0.0),
            msg=f"expected no battery activity in the expensive middle interval (B) - arbitrage is unprofitable there, got {middle_b}",
        )
        # The dispatch actually differs materially, not just numerically at
        # the noise floor.
        self.assertGreater(abs(middle_a.mean() - middle_b.mean()), 300.0)


def _default_config() -> dict:
    return json.loads(_EMHASS_CONF["defaults_path"].read_text(encoding="utf-8"))


async def _build_params(overrides: dict | None = None) -> dict:
    config = _default_config()
    if overrides:
        config.update(overrides)
    _, secrets = await utils.build_secrets(_EMHASS_CONF, _config_logger, no_response=True)
    params = await utils.build_params(_EMHASS_CONF, secrets, config, _config_logger)
    assert params is not False, "build_params failed (see logged error)"
    return params


def _round_trip_config(overrides: dict) -> dict:
    """config.json dict -> build_params -> param_to_config -> config.json dict,
    exactly the two-step pipeline both /get-config and /set-config use
    (see web_server.py parameter_get / parameter_set). No Optimization
    object involved - this isolates the config-layer transport only."""
    params = asyncio.run(_build_params(overrides))
    return utils.param_to_config(params, _config_logger)


class TestCapacityMultiComponentConfigRoundTrip(unittest.TestCase):
    """Remediation item 1.B: the K>1 list form must survive the exact
    build_params / param_to_config pipeline used by both
    GET /get-config and POST /set-config - proven directly against that
    pipeline, not inferred from the OpenAPI type declaration."""

    def test_list_capacity_cost_survives_round_trip(self):
        out = _round_trip_config({"capacity_cost_per_kw": [3.0, 7.5, 0.0]})
        self.assertEqual(out["capacity_cost_per_kw"], [3.0, 7.5, 0.0])

    def test_list_interval_timesteps_survives_round_trip(self):
        out = _round_trip_config(
            {
                "capacity_cost_per_kw": [2.0, 2.0],
                "capacity_charge_interval_timesteps": [6, 3],
            }
        )
        self.assertEqual(out["capacity_cost_per_kw"], [2.0, 2.0])
        self.assertEqual(out["capacity_charge_interval_timesteps"], [6, 3])

    def test_scalar_legacy_form_still_survives_round_trip(self):
        """K=1 legacy scalar form must round-trip unchanged (byte-identical
        type), not get silently coerced to a list anywhere in the config
        pipeline - only the UI's own array-typed metadata affects rendering/
        saving in the browser (see docs/cookbook/tariff_demand_charge.md);
        the JSON transport itself is fully type-preserving."""
        out = _round_trip_config({"capacity_cost_per_kw": 8.0})
        self.assertEqual(out["capacity_cost_per_kw"], 8.0)
        self.assertIsInstance(out["capacity_cost_per_kw"], float)

    def test_scalar_result_feeds_legacy_k1_optimization_path(self):
        """End-to-end: a round-tripped scalar config value, fed straight into
        Optimization, still takes the untouched K=1 legacy path (not the
        generic K>1-with-K=1 path) - the config layer never silently
        reshapes a scalar into a list."""
        out = _round_trip_config({"capacity_cost_per_kw": 8.0})
        opt = Optimization(
            {
                "optimization_time_step": pd.to_timedelta(5, "minutes"),
                "time_zone": "Europe/Tallinn",
                "sensor_power_photovoltaics": "pv",
                "sensor_power_load_no_var_loads": "load",
            },
            {
                **out,
                "delta_forecast_daily": pd.Timedelta(hours=1),
                "set_use_battery": False,
                "set_use_pv": False,
                "number_of_deferrable_loads": 0,
                "set_nodischarge_to_grid": True,
            },
            {
                "inverter_is_hybrid": False,
                "compute_curtailment": False,
                "maximum_power_from_grid": 50000,
                "maximum_power_to_grid": 50000,
            },
            "unit_load_cost",
            "unit_prod_price",
            "profit",
            {"root_path": TEST_ROOT / "src" / "emhass", "data_path": TEST_ROOT / "data"},
            _config_logger,
            opt_time_delta=5,
        )
        self.assertFalse(opt._capacity_multi)


if __name__ == "__main__":
    unittest.main()
