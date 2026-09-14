#!/usr/bin/env python3

"""Tests for the `pv-bias-calibration` action wrapper (issue #1128).

`emhass.command_line.pv_bias_calibration` is a thin, side-effect-free wrapper
around `emhass.pv_bias_calibration.compute_pv_bias_calibration`: it only reads
the caller-supplied history from `input_data_dict["params"]["passed_data"]`
and returns the engine's own result. These tests exercise the wrapper's
input handling directly (required keys, optional kwargs, error mapping)
without needing a live Home Assistant connection or a Forecast/Optimization
object -- the wrapper never touches either.
"""

import asyncio
import logging
import unittest

logger = logging.getLogger("test_pv_bias_calibration_action")


def run(coro):
    return asyncio.run(coro)


try:
    from emhass.command_line import pv_bias_calibration
except Exception:  # pragma: no cover - only hit on the base branch
    pv_bias_calibration = None


def _input_data_dict(passed_data: dict) -> dict:
    return {"params": {"passed_data": passed_data}}


@unittest.skipIf(pv_bias_calibration is None, "pv_bias_calibration action not present (base branch)")
class TestPvBiasCalibrationAction(unittest.TestCase):
    def test_valid_payload_returns_engine_result(self):
        passed_data = {
            "p10": [1.0, 1.0, 1.0, 1.0, 1.0],
            "p50": [4.0, 4.0, 4.0, 4.0, 4.0],
            "actual": [2.0, 5.0, 1.5, 4.5, 3.0],
            "target_shortfall_rate": 0.4,
        }
        result = run(pv_bias_calibration(_input_data_dict(passed_data), logger))
        self.assertIsNotNone(result)
        for key in (
            "recommended_bias",
            "achieved_shortfall_rate",
            "feasible_shortfall_range",
            "target_feasible",
            "converged",
            "n_observations",
            "n_curtailed_excluded",
        ):
            self.assertIn(key, result)
        self.assertEqual(result["n_observations"], 5)

    def test_missing_required_inputs_returns_none(self):
        for passed_data in (
            {"p50": [1.0, 2.0], "actual": [1.0, 2.0]},  # missing p10
            {"p10": [1.0, 2.0], "actual": [1.0, 2.0]},  # missing p50
            {"p10": [1.0, 2.0], "p50": [1.0, 2.0]},  # missing actual
            {},  # missing everything
        ):
            with self.subTest(passed_data=passed_data):
                result = run(pv_bias_calibration(_input_data_dict(passed_data), logger))
                self.assertIsNone(result)

    def test_invalid_history_returns_none_not_raise(self):
        # Length mismatch: the underlying engine raises ValueError, the action
        # wrapper must catch it and report failure rather than propagating.
        passed_data = {"p10": [1.0, 2.0], "p50": [1.0], "actual": [1.0, 2.0]}
        result = run(pv_bias_calibration(_input_data_dict(passed_data), logger))
        self.assertIsNone(result)

    def test_infeasible_target_reported_honestly(self):
        # A target_shortfall_rate below what the P10/P50 blend can express
        # (actual always exceeds P50) must be reported as infeasible/not
        # converged, never silently coerced to a feasible-looking result.
        n = 30
        passed_data = {
            "p10": [1.0] * n,
            "p50": [2.0] * n,
            "actual": [10.0] * n,  # always above P50 => shortfall rate is 0 everywhere
            "target_shortfall_rate": 0.5,
        }
        result = run(pv_bias_calibration(_input_data_dict(passed_data), logger))
        self.assertIsNotNone(result)
        self.assertFalse(result["target_feasible"])
        self.assertFalse(result["converged"])

    def test_curtailment_exclusions_reported(self):
        passed_data = {
            "p10": [1.0, 1.0, 1.0, 1.0, 1.0],
            "p50": [4.0, 4.0, 4.0, 4.0, 4.0],
            "actual": [2.0, 5.0, 1.5, 4.5, 0.1],
            "curtailed": [False, False, False, False, True],
        }
        result = run(pv_bias_calibration(_input_data_dict(passed_data), logger))
        self.assertIsNotNone(result)
        self.assertEqual(result["n_curtailed_excluded"], 1)
        self.assertEqual(result["n_observations"], 4)

    def test_no_optimization_or_config_side_effects(self):
        # The wrapper must not mutate passed_data beyond reading it, and must
        # never touch optim_conf / weather_forecast_pv_quantile_bias -- it
        # only ever receives `params`, so there is nothing for it to write
        # back into optim_conf even by accident.
        passed_data = {
            "p10": [1.0, 1.0, 1.0],
            "p50": [4.0, 4.0, 4.0],
            "actual": [2.0, 5.0, 1.5],
        }
        input_data_dict = _input_data_dict(passed_data)
        before = dict(passed_data)
        run(pv_bias_calibration(input_data_dict, logger))
        self.assertEqual(passed_data, before)
        self.assertNotIn("fcst", input_data_dict)
        self.assertNotIn("opt", input_data_dict)


if __name__ == "__main__":
    unittest.main()
