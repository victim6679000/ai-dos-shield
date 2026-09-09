"""Fit the shield's cost model against the real inference service.

The competition protocol is intentionally fixed: four input sizes, four
output limits, and three repeats per cell. The median of each cell is fitted
with least squares:

    model_ms ~= c0 + c_in * input_tokens + c_out * max_new_tokens

The inference API reports the effective tokenizer input length after its
512-token truncation. Calibration therefore fits work the model actually
performed instead of treating characters as tokens.

Run from any directory with the real service already healthy:

    python analysis/calibrate.py --target http://localhost:8000
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "results" / "raw" / "calibration.json"
INPUT_CHAR_LENGTHS = (40, 400, 1200, 3000)
OUTPUT_TOKEN_LIMITS = (8, 32, 96, 192)
REQUIRED_REPEATS = 3
WARMUP_REQUESTS = 3
CALIBRATION_CLIENT_ID = "calibration"


def _git_value(*args: str) -> str | None:
    """Return a Git value when available without making Git a requirement."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _git_metadata() -> dict[str, Any]:
    status = _git_value("status", "--porcelain")
    return {
        "commit": _git_value("rev-parse", "HEAD"),
        "branch": _git_value("branch", "--show-current"),
        "dirty": bool(status) if status is not None else None,
    }


def _require_real_health(client: httpx.Client, target: str) -> dict[str, Any]:
    try:
        response = client.get(f"{target}/health")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"cannot reach {target}/health: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(f"health returned HTTP {response.status_code}: {response.text[:200]}")

    try:
        health = response.json()
    except ValueError as exc:
        raise RuntimeError("health did not return valid JSON") from exc

    if health.get("status") != "ok" or health.get("model_ready") is not True:
        raise RuntimeError(f"inference service is not ready: {health}")
    if health.get("mock") is not False:
        raise RuntimeError(
            "calibration requires MOCK_MODEL=false; the target is currently using the mock model"
        )

    required_metadata = (
        "model",
        "workers",
        "torch_threads",
        "max_input_tokens",
        "max_new_tokens_cap",
    )
    missing = [key for key in required_metadata if key not in health]
    if missing:
        raise RuntimeError(
            "inference health is missing calibration metadata: " + ", ".join(missing)
        )
    if max(OUTPUT_TOKEN_LIMITS) > int(health["max_new_tokens_cap"]):
        raise RuntimeError("calibration output grid exceeds the server's max_new_tokens cap")

    return health


def _request_sample(
    client: httpx.Client,
    target: str,
    text: str,
    max_new_tokens: int,
) -> dict[str, Any]:
    try:
        response = client.post(
            f"{target}/infer",
            json={"text": text, "max_new_tokens": max_new_tokens},
            headers={"X-Client-ID": CALIBRATION_CLIENT_ID},
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"inference request failed: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"inference returned HTTP {response.status_code}: {response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("inference did not return valid JSON") from exc

    required = ("request_id", "output", "model_ms", "input_tokens")
    missing = [key for key in required if key not in payload]
    if missing:
        raise RuntimeError("inference response is missing: " + ", ".join(missing))

    model_ms = payload["model_ms"]
    input_tokens = payload["input_tokens"]
    if isinstance(model_ms, bool) or not isinstance(model_ms, (int, float)):
        raise RuntimeError("model_ms must be numeric")
    if not math.isfinite(float(model_ms)) or float(model_ms) <= 0:
        raise RuntimeError(f"model_ms must be finite and positive, got {model_ms!r}")
    if isinstance(input_tokens, bool) or not isinstance(input_tokens, int) or input_tokens <= 0:
        raise RuntimeError(f"input_tokens must be a positive integer, got {input_tokens!r}")
    if not isinstance(payload["request_id"], str) or not payload["request_id"]:
        raise RuntimeError("request_id must be a non-empty string")
    if not isinstance(payload["output"], str):
        raise RuntimeError("output must be a string")

    return {
        "request_id": payload["request_id"],
        "model_ms": float(model_ms),
        "input_tokens": input_tokens,
        "output_chars": len(payload["output"]),
    }


def _fit_cost_model(cells: list[dict[str, Any]]) -> dict[str, Any]:
    expected_cells = len(INPUT_CHAR_LENGTHS) * len(OUTPUT_TOKEN_LIMITS)
    if len(cells) != expected_cells:
        raise RuntimeError(f"expected {expected_cells} complete cells, got {len(cells)}")

    design = np.array(
        [[1.0, row["input_tokens"], row["max_new_tokens"]] for row in cells],
        dtype=float,
    )
    measured = np.array([row["model_ms"] for row in cells], dtype=float)
    if np.linalg.matrix_rank(design) != 3:
        raise RuntimeError("calibration design matrix is rank deficient")

    coefficients, *_ = np.linalg.lstsq(design, measured, rcond=None)
    if not np.all(np.isfinite(coefficients)):
        raise RuntimeError("fit produced a non-finite coefficient")
    if np.any(coefficients < 0):
        raise RuntimeError(
            "fit produced a negative coefficient; repeat the real measurements before use"
        )

    predicted = design @ coefficients
    residual_sum = float(np.sum((measured - predicted) ** 2))
    total_sum = float(np.sum((measured - measured.mean()) ** 2))
    if total_sum <= 0:
        raise RuntimeError("all measurements are identical; R^2 is undefined")
    r2 = 1.0 - residual_sum / total_sum
    if not math.isfinite(r2):
        raise RuntimeError("fit produced a non-finite R^2")

    c0, c_in, c_out = (float(value) for value in coefficients)
    for row, predicted_ms in zip(cells, predicted, strict=True):
        row["predicted_model_ms"] = round(float(predicted_ms), 6)
        row["residual_model_ms"] = round(row["model_ms"] - float(predicted_ms), 6)

    cheapest = min(cells, key=lambda row: row["predicted_model_ms"])
    most_expensive = max(cells, key=lambda row: row["predicted_model_ms"])
    cheapest_ms = float(cheapest["predicted_model_ms"])
    most_expensive_ms = float(most_expensive["predicted_model_ms"])
    if cheapest_ms <= 0:
        raise RuntimeError("fit predicts a non-positive cheapest request")

    return {
        "equation": (
            f"model_ms = {c0:.3f} + {c_in:.6f}*input_tokens "
            f"+ {c_out:.6f}*max_new_tokens"
        ),
        "c0": c0,
        "c_in": c_in,
        "c_out": c_out,
        "r2": r2,
        "cheapest": {
            "input_chars": cheapest["input_chars"],
            "input_tokens": cheapest["input_tokens"],
            "max_new_tokens": cheapest["max_new_tokens"],
            "predicted_model_ms": cheapest_ms,
        },
        "most_expensive": {
            "input_chars": most_expensive["input_chars"],
            "input_tokens": most_expensive["input_tokens"],
            "max_new_tokens": most_expensive["max_new_tokens"],
            "predicted_model_ms": most_expensive_ms,
        },
        "cost_ratio": most_expensive_ms / cheapest_ms,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_calibration(args: argparse.Namespace) -> dict[str, Any]:
    if args.repeats != REQUIRED_REPEATS:
        raise RuntimeError(
            f"the fixed competition protocol requires exactly {REQUIRED_REPEATS} repeats"
        )

    target = args.target.rstrip("/")
    raw_samples: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    warmup_samples: list[dict[str, Any]] = []

    with httpx.Client(timeout=args.timeout) as client:
        health = _require_real_health(client, target)
        print(
            f"real model ready: {health['model']} "
            f"(workers={health['workers']}, torch_threads={health['torch_threads']})"
        )

        print(f"warming up with {WARMUP_REQUESTS} requests...")
        for warmup_index in range(1, WARMUP_REQUESTS + 1):
            sample = _request_sample(client, target, "warm up", 8)
            sample["warmup"] = warmup_index
            warmup_samples.append(sample)
            time.sleep(args.pause_s)

        for input_chars, max_new_tokens in itertools.product(
            INPUT_CHAR_LENGTHS,
            OUTPUT_TOKEN_LIMITS,
        ):
            text = ("word " * ((input_chars + 4) // 5))[:input_chars]
            cell_measurements: list[float] = []
            cell_input_tokens: int | None = None

            for repeat in range(1, args.repeats + 1):
                sample = _request_sample(client, target, text, max_new_tokens)
                if cell_input_tokens is None:
                    cell_input_tokens = sample["input_tokens"]
                elif sample["input_tokens"] != cell_input_tokens:
                    raise RuntimeError("tokenizer input length changed between identical repeats")

                cell_measurements.append(sample["model_ms"])
                raw_samples.append(
                    {
                        "input_chars": len(text),
                        "input_tokens": sample["input_tokens"],
                        "max_new_tokens": max_new_tokens,
                        "repeat": repeat,
                        **sample,
                    }
                )
                time.sleep(args.pause_s)

            if len(cell_measurements) != REQUIRED_REPEATS or cell_input_tokens is None:
                raise RuntimeError("a calibration cell did not complete all required repeats")

            median_ms = float(np.median(cell_measurements))
            cell = {
                "input_chars": len(text),
                "input_tokens": cell_input_tokens,
                "max_new_tokens": max_new_tokens,
                "repeat_count": len(cell_measurements),
                "sample_model_ms": cell_measurements,
                "model_ms": median_ms,
            }
            cells.append(cell)
            print(
                f"  in={len(text):>4}ch/{cell_input_tokens:>3}tok "
                f"out={max_new_tokens:>3}tok -> median {median_ms:8.2f} ms "
                f"samples={','.join(f'{value:.2f}' for value in cell_measurements)}"
            )

    expected_samples = len(INPUT_CHAR_LENGTHS) * len(OUTPUT_TOKEN_LIMITS) * REQUIRED_REPEATS
    if len(raw_samples) != expected_samples:
        raise RuntimeError(f"expected {expected_samples} raw samples, got {len(raw_samples)}")

    fit = _fit_cost_model(cells)
    created_utc = datetime.now(timezone.utc).isoformat()
    result = {
        "schema_version": 2,
        "created_utc": created_utc,
        "target": target,
        "health": health,
        "git": _git_metadata(),
        "protocol": {
            "input_char_lengths": list(INPUT_CHAR_LENGTHS),
            "output_token_limits": list(OUTPUT_TOKEN_LIMITS),
            "repeats_per_cell": REQUIRED_REPEATS,
            "warmup_requests": WARMUP_REQUESTS,
            "pause_s": args.pause_s,
            "timing_scope": (
                "model.generate only; excludes tokenization, decoding, queueing, and network"
            ),
            "fit_observation": "median model_ms for each of 16 cells",
            "input_feature": "effective tokenizer tokens after server truncation",
        },
        "warmup_samples": warmup_samples,
        "raw_samples": raw_samples,
        # Keep `samples` and the top-level coefficients compatible with the
        # original artifact while adding the full evidence above.
        "samples": cells,
        "fit": fit,
        "c0": fit["c0"],
        "c_in": fit["c_in"],
        "c_out": fit["c_out"],
        "r2": fit["r2"],
        "cost_ratio": fit["cost_ratio"],
    }

    output_path = Path(args.output).resolve()
    _write_json_atomic(output_path, result)

    print(f"\n  {fit['equation']}")
    print(f"  R^2 = {fit['r2']:.4f}   (report this number in the PDF)")
    print(
        "\n  cheapest request modelled: "
        f"{fit['cheapest']['predicted_model_ms']:.0f} ms "
        f"({fit['cheapest']['input_tokens']} in, "
        f"{fit['cheapest']['max_new_tokens']} out)"
    )
    print(
        "  most expensive modelled  : "
        f"{fit['most_expensive']['predicted_model_ms']:.0f} ms "
        f"({fit['most_expensive']['input_tokens']} in, "
        f"{fit['most_expensive']['max_new_tokens']} out)"
    )
    print(f"  cost ratio               : {fit['cost_ratio']:.1f}x")
    print("\n--- hand to Person 3 / paste into .env ---")
    print(f"COST_C0={fit['c0']:.3f}")
    print(f"COST_C_IN={fit['c_in']:.6f}")
    print(f"COST_C_OUT={fit['c_out']:.6f}")
    print("\n--- calibrated mock timing ---")
    print(f"MOCK_BASE_MS={fit['c0']:.3f}")
    print(f"MOCK_MS_PER_IN_TOKEN={fit['c_in']:.6f}")
    print(f"MOCK_MS_PER_OUT_TOKEN={fit['c_out']:.6f}")
    print(f"\nsaved -> {output_path}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="http://localhost:8000")
    parser.add_argument("--repeats", type=int, default=REQUIRED_REPEATS)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--pause-s", type=float, default=0.05)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


if __name__ == "__main__":
    try:
        run_calibration(build_parser().parse_args())
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"calibration failed: {exc}") from exc
