"""Regression tests for optional post-RAW14 first-step PV override (#541).

No HA, Sigenergy or physical dispatch required.
"""

import copy
import pathlib
import shutil
import tempfile
import unittest
from datetime import UTC, datetime

import numpy as np
import orjson
import pandas as pd
import time_machine

from emhass import utils
from emhass.command_line import (
    OptimizationCache,
    _apply_first_step_pv_override,
    naive_mpc_optim,
    publish_data,
    set_input_data_dict,
)


class TestFirstStepPVOverride(unittest.TestCase):
    def setUp(self):
        self.grid = pd.date_range("2026-10-18T06:00:00Z", periods=3, freq="5min")
        self.adjusted = pd.Series([4200.0, 4400.0, 4600.0], index=self.grid)

    def test_changes_only_first_native_adjusted_slot(self):
        source = self.adjusted.copy(deep=True)
        actual = _apply_first_step_pv_override(
            source, {"timestamp": "2026-10-18T17:00:00+11:00", "power_w": 2050}
        )
        self.assertEqual(actual.tolist(), [2050.0, 4400.0, 4600.0])
        pd.testing.assert_series_equal(source, self.adjusted)
        self.assertEqual(actual.index.tolist(), self.adjusted.index.tolist())

    def test_valid_zero_and_equivalent_utc_timestamp(self):
        actual = _apply_first_step_pv_override(
            self.adjusted, {"timestamp": "2026-10-18T06:00:00Z", "power_w": 0}
        )
        self.assertEqual(actual.iloc[0], 0)

    def test_wrong_grid_step_fails_closed(self):
        for timestamp in (
            "2026-10-18T17:05:00+11:00",
            "2026-10-18T17:00:00+10:00",
            "2026-10-18T17:00:00",  # naive wall time
            "not-a-time",
        ):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(ValueError):
                    _apply_first_step_pv_override(
                        self.adjusted, {"timestamp": timestamp, "power_w": 1000}
                    )

    def test_bad_power_or_schema_fails_closed(self):
        for override in (
            None,
            [],
            {},
            {"timestamp": "2026-10-18T06:00:00Z", "power_w": -1},
            {"timestamp": "2026-10-18T06:00:00Z", "power_w": True},
            {"timestamp": "2026-10-18T06:00:00Z", "power_w": float("nan")},
            {"timestamp": "2026-10-18T06:00:00Z", "power_w": float("inf")},
            {"timestamp": "2026-10-18T06:00:00Z", "power_w": "2000"},
            {"timestamp": "2026-10-18T06:00:00Z", "power_w": 2000, "extra": 1},
        ):
            with self.subTest(override=repr(override)):
                with self.assertRaises(ValueError):
                    _apply_first_step_pv_override(self.adjusted, override)

    def test_naive_pv_grid_rejected(self):
        data = self.adjusted.copy()
        data.index = data.index.tz_localize(None)
        with self.assertRaises(ValueError):
            _apply_first_step_pv_override(
                data, {"timestamp": "2026-10-18T06:00:00Z", "power_w": 10}
            )


root = pathlib.Path(utils.get_root(__file__, num_parent=2))
_tmp = tempfile.TemporaryDirectory(prefix="emhass-pv-override-")
shutil.copy(root / "data/test_df_final.pkl", _tmp.name)  # history the file-mode RAW14 fit reads
emhass_conf = {
    "data_path": pathlib.Path(_tmp.name),
    "root_path": root / "src/emhass/",
    "defaults_path": root / "src/emhass/data/config_defaults.json",
    "associations_path": root / "src/emhass/data/associations.csv",
    "config_path": root / "config.json",
}
logger, _ = utils.get_logger(__name__, emhass_conf, save_to_file=False)

HORIZON = 12
NOW = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)  # 17:00 AEDT
# Sydney is still on AEST (+10:00) here; clocks go forward at 16:00Z.
DST_EVE = datetime(2026, 10, 3, 15, 50, tzinfo=UTC)
GOOD = "2026-10-18T17:00:00+11:00"


class TestNaiveMPCOverrideRealPath(unittest.IsolatedAsyncioTestCase):
    """Real runtimeparams -> RAW14 -> override -> CVXPY -> publish path."""

    async def asyncSetUp(self):
        OptimizationCache.clear()
        config = await utils.build_config(emhass_conf, logger, emhass_conf["defaults_path"])
        _, secrets = await utils.build_secrets(emhass_conf, logger, no_response=True)
        self.params = await utils.build_params(emhass_conf, secrets, config, logger)
        self.params["optim_conf"].update(
            set_use_pv=True,
            set_use_adjusted_pv=True,  # native RAW14 runs before the override
            set_use_battery=True,
            number_of_deferrable_loads=0,  # default 5 h loads cannot fit a 1 h horizon
            weather_forecast_method="list",
            load_forecast_method="list",
            load_cost_forecast_method="list",
            production_price_forecast_method="list",
        )
        self.params["retrieve_hass_conf"]["time_zone"] = "Australia/Sydney"
        self.params["retrieve_hass_conf"]["optimization_time_step"] = 5

    def tearDown(self):
        OptimizationCache.clear()

    def _runtime(self, n=HORIZON, **extra):
        runtime = {
            "pv_power_forecast": [1500.0 + 20 * i for i in range(n)],
            "load_power_forecast": [900.0 + 10 * i for i in range(n)],
            "load_cost_forecast": [0.30 + 0.01 * i for i in range(n)],
            "prod_price_forecast": [0.05] * n,
            "prediction_horizon": HORIZON,
            "soc_init": 0.5,
            "soc_final": 0.5,
        }
        runtime.update(extra)
        return runtime

    async def _inputs(self, runtime, now=NOW, action="naive-mpc-optim"):
        params = copy.deepcopy(self.params)
        with time_machine.travel(now, tick=False):
            return await set_input_data_dict(
                emhass_conf,
                "profit",
                orjson.dumps(params).decode("utf-8"),
                orjson.dumps(runtime).decode("utf-8"),
                action,
                logger,
                get_data_from_file=True,
            )

    async def _solve(self, runtime, now=NOW):
        data = await self._inputs(runtime, now)
        self.assertIsInstance(data, dict)
        return data, await naive_mpc_optim(data, logger, debug=True)

    async def test_absent_is_noop_and_present_changes_only_first_pv_value(self):
        base, base_res = await self._solve(self._runtime())
        override = {"timestamp": GOOD, "power_w": 1234}
        over, over_res = await self._solve(self._runtime(pv_power_first_step_override=override))
        # The caller-supplied instant is the first optimiser row.
        self.assertEqual(
            over["df_input_data_dayahead"].index[0], pd.Timestamp("2026-10-18T06:00:00Z")
        )
        base_pv, over_pv = base["p_pv_forecast"], over["p_pv_forecast"]
        self.assertEqual(over_pv.iloc[0], 1234.0)
        self.assertNotEqual(base_pv.iloc[0], 1234.0)
        np.testing.assert_array_equal(over_pv.iloc[1:].values, base_pv.iloc[1:].values)
        np.testing.assert_array_equal(
            over["df_input_data_dayahead"].drop(columns="p_pv_forecast").values,
            base["df_input_data_dayahead"].drop(columns="p_pv_forecast").values,
        )
        np.testing.assert_array_equal(
            over["p_load_forecast"].values, base["p_load_forecast"].values
        )
        # CVXPY PV parameter, solved P_PV and the rest of the horizon.
        self.assertEqual(over["opt"].param_pv_forecast.value[0], 1234.0)
        np.testing.assert_array_equal(
            over["opt"].param_pv_forecast.value[1:], base["opt"].param_pv_forecast.value[1:]
        )
        self.assertAlmostEqual(over_res["P_PV"].iloc[0], 1234.0, places=3)
        np.testing.assert_allclose(over_res["P_Load"], base_res["P_Load"])
        self.assertNotIn("pv_power_first_step_override", base["params"]["passed_data"])

    async def test_cache_hit_consumes_override_without_leaking(self):
        first, _ = await self._solve(
            self._runtime(pv_power_first_step_override={"timestamp": GOOD, "power_w": 1234})
        )
        key = OptimizationCache.get_stats()["cache_key"]
        second, res2 = await self._solve(
            self._runtime(pv_power_first_step_override={"timestamp": GOOD, "power_w": 0})
        )
        # Same structure -> cache hit: same Optimization, new PV parameter values.
        self.assertIs(second["opt"], first["opt"])
        self.assertEqual(OptimizationCache.get_stats()["cache_key"], key)
        self.assertEqual(second["opt"].param_pv_forecast.value[0], 0.0)
        self.assertAlmostEqual(res2["P_PV"].iloc[0], 0.0, places=3)
        # Omitting the field afterwards restores the native first value.
        third, res3 = await self._solve(self._runtime())
        self.assertIs(third["opt"], first["opt"])
        self.assertEqual(third["opt"].param_pv_forecast.value[0], third["p_pv_forecast"].iloc[0])
        self.assertAlmostEqual(res3["P_PV"].iloc[0], third["p_pv_forecast"].iloc[0], places=3)

    async def test_publish_consumes_overridden_first_row(self):
        data, res = await self._solve(
            self._runtime(pv_power_first_step_override={"timestamp": GOOD, "power_w": 777})
        )
        published = await publish_data(data, logger, opt_res_latest=res, dont_post=True)
        self.assertAlmostEqual(published["P_PV"].iloc[0], 777.0, places=3)
        self.assertEqual(published.index[0], pd.Timestamp("2026-10-18T06:00:00Z"))

    async def test_invalid_override_fails_before_optimisation(self):
        for override in (
            {"timestamp": GOOD, "power_w": True},
            {"timestamp": GOOD, "power_w": float("nan")},
            {"timestamp": GOOD, "power_w": -0.5},
            {"timestamp": "2026-10-18T17:00:00", "power_w": 10},  # naive
            {"timestamp": "2026-10-18T17:00:00+10:00", "power_w": 10},  # wrong offset
            {"timestamp": "2026-10-18T16:55:00+11:00", "power_w": 10},  # stale
            {"timestamp": "2026-10-18T17:05:00+11:00", "power_w": 10},  # future
            {"timestamp": "2026-10-18T17:00:30+11:00", "power_w": 10},  # off-grid
            None,
        ):
            with self.subTest(override=repr(override)):
                runtime = self._runtime(pv_power_first_step_override=override)
                with self.assertLogs(level="ERROR") as logs:
                    data = await self._inputs(runtime)
                self.assertIs(data, False)
                self.assertTrue(any("PV first-step override" in m for m in logs.output))

    async def test_sydney_dst_boundary_uses_utc_instant(self):
        # 01:50 AEST, ten minutes before clocks go forward.
        ok = {"timestamp": "2026-10-04T01:50:00+10:00", "power_w": 500}
        data, res = await self._solve(self._runtime(pv_power_first_step_override=ok), DST_EVE)
        self.assertEqual(data["df_input_data_dayahead"].index[0], pd.Timestamp("2026-10-03T15:50Z"))
        self.assertAlmostEqual(res["P_PV"].iloc[0], 500.0, places=3)
        wrong = {"timestamp": "2026-10-04T01:50:00+11:00", "power_w": 500}  # 14:50Z
        runtime = self._runtime(pv_power_first_step_override=wrong)
        self.assertIs(await self._inputs(runtime, DST_EVE), False)

    async def test_not_stored_for_other_actions(self):
        runtime = self._runtime(
            n=288, pv_power_first_step_override={"timestamp": GOOD, "power_w": 1}
        )
        data = await self._inputs(runtime, action="dayahead-optim")
        self.assertNotIn("pv_power_first_step_override", data["params"]["passed_data"])


if __name__ == "__main__":
    unittest.main()
