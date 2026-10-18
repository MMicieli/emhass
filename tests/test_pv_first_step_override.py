"""Regression tests for optional post-RAW14 first-step PV override (#541).

No HA, Sigenergy or physical dispatch required.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd

from emhass.command_line import (
    _apply_first_step_pv_override,
    _prepare_naive_mpc_optim,
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


class TestNaiveMPCOverridePlacement(unittest.IsolatedAsyncioTestCase):
    async def _prepare(self, override=None, supplied=False):
        grid = pd.date_range("2026-10-18T06:00:00Z", periods=3, freq="5min")
        pv = pd.Series([4200.0, 4400.0, 4600.0], index=grid)
        load = pd.Series([1200.0, 1300.0, 1400.0], index=grid)
        passed = {"prediction_horizon": 3}
        if supplied:
            passed["pv_power_first_step_override"] = override
        ctx = SimpleNamespace(
            fcst=SimpleNamespace(get_load_forecast=AsyncMock(return_value=load)),
            optim_conf={"delta_forecast_daily": pd.Timedelta(days=1)},
            retrieve_hass_conf={"optimization_time_step": pd.Timedelta(minutes=5)},
            params={"passed_data": passed},
            logger=MagicMock(),
        )
        with (
            patch(
                "emhass.command_line._get_naive_mpc_history",
                new_callable=AsyncMock,
                return_value=(True, pd.DataFrame(), [], False),
            ),
            patch(
                "emhass.command_line._get_naive_mpc_pv_forecast",
                new_callable=AsyncMock,
                return_value=(pv, None),
            ),
        ):
            return await _prepare_naive_mpc_optim(ctx)

    async def test_omission_preserves_native_raw14_output(self):
        result = await self._prepare()
        self.assertEqual(result["p_pv_forecast"].tolist(), [4200, 4400, 4600])
        self.assertEqual(result["df_input_data_dayahead"]["p_pv_forecast"].tolist(),
                         [4200, 4400, 4600])

    async def test_override_consumed_after_adjustment_before_solver_input(self):
        result = await self._prepare(
            {"timestamp": "2026-10-18T17:00:00+11:00", "power_w": 2050},
            supplied=True,
        )
        self.assertEqual(result["p_pv_forecast"].tolist(), [2050, 4400, 4600])
        self.assertEqual(
            result["df_input_data_dayahead"]["p_pv_forecast"].tolist(),
            [2050, 4400, 4600],
        )
        self.assertEqual(
            result["df_input_data_dayahead"]["p_load_forecast"].tolist(),
            [1200, 1300, 1400],
        )

    async def test_present_but_invalid_override_does_not_silently_fallback(self):
        with self.assertRaises(ValueError):
            await self._prepare(None, supplied=True)


if __name__ == "__main__":
    unittest.main()
