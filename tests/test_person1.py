"""Deterministic tests for Person 1's timing and calibration logic."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from analysis import calibrate
from inference import app as inference_app


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def perf_counter(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTensor:
    shape = (1, 42)


class FakeTokenizer:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    def __call__(self, *_args, **_kwargs):
        self.clock.advance(0.100)
        return {"input_ids": FakeTensor()}

    def decode(self, *_args, **_kwargs) -> str:
        self.clock.advance(0.150)
        return "decoded output"


class FakeModel:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    def generate(self, **_kwargs):
        self.clock.advance(0.250)
        return [[1, 2, 3]]


class InferenceTimingTests(unittest.TestCase):
    def test_real_timing_excludes_tokenization_and_decode(self) -> None:
        clock = FakeClock()
        with (
            patch.object(inference_app, "_tokenizer", FakeTokenizer(clock)),
            patch.object(inference_app, "_model", FakeModel(clock)),
            patch.object(inference_app.time, "perf_counter", clock.perf_counter),
        ):
            output, model_ms, input_tokens = inference_app._run_real("hello", 8)

        self.assertEqual(output, "decoded output")
        self.assertEqual(input_tokens, 42)
        self.assertAlmostEqual(model_ms, 250.0)
        self.assertAlmostEqual(clock.now, 0.500)

    def test_mock_timing_measures_only_simulated_forward_pass(self) -> None:
        clock = FakeClock()
        with (
            patch.object(inference_app, "MOCK_BASE_MS", 60.0),
            patch.object(inference_app, "MOCK_MS_PER_IN_TOKEN", 0.8),
            patch.object(inference_app, "MOCK_MS_PER_OUT_TOKEN", 18.0),
            patch.object(inference_app, "MOCK_JITTER", 0.0),
            patch.object(inference_app.random, "uniform", return_value=0.0),
            patch.object(inference_app.time, "perf_counter", clock.perf_counter),
            patch.object(inference_app.time, "sleep", clock.advance),
        ):
            output, model_ms, input_tokens = inference_app._run_mock("x" * 40, 8)

        self.assertEqual(output, "[mock output for 8 tokens]")
        self.assertEqual(input_tokens, 10)
        self.assertAlmostEqual(model_ms, 212.0)


class CalibrationTests(unittest.TestCase):
    def test_exact_linear_data_recovers_coefficients(self) -> None:
        cells = []
        for input_chars, input_tokens in zip(
            calibrate.INPUT_CHAR_LENGTHS,
            (10, 82, 242, 512),
            strict=True,
        ):
            for max_new_tokens in calibrate.OUTPUT_TOKEN_LIMITS:
                cells.append(
                    {
                        "input_chars": input_chars,
                        "input_tokens": input_tokens,
                        "max_new_tokens": max_new_tokens,
                        "model_ms": 100.0 + 0.5 * input_tokens + 2.0 * max_new_tokens,
                    }
                )

        fit = calibrate._fit_cost_model(cells)
        self.assertAlmostEqual(fit["c0"], 100.0)
        self.assertAlmostEqual(fit["c_in"], 0.5)
        self.assertAlmostEqual(fit["c_out"], 2.0)
        self.assertAlmostEqual(fit["r2"], 1.0)

    def test_mock_health_is_rejected(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"status": "ok", "model_ready": True, "mock": True},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(RuntimeError, "MOCK_MODEL=false"):
                calibrate._require_real_health(client, "http://test")


if __name__ == "__main__":
    unittest.main()
