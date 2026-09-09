"""
Cost model - the core idea of this project.

A conventional rate limiter charges 1 unit per request. That is wrong for
inference: a request asking for 512 output tokens costs ~50x one asking
for 8. So we charge clients in ESTIMATED MILLISECONDS OF MODEL TIME.

The coefficients below are not guesses. analysis/calibrate.py runs a sweep
against the real inference server, fits

    model_ms ~= C0 + C_IN * input_tokens + C_OUT * max_new_tokens

by least squares, reports R^2, and prints the env lines to paste here.
That is what lets us say the limiter prices compute rather than HTTP.
"""

import os

COST_C0 = float(os.getenv("COST_C0", "60"))
COST_C_IN = float(os.getenv("COST_C_IN", "0.8"))
COST_C_OUT = float(os.getenv("COST_C_OUT", "18"))
COST_MAX_MS = float(os.getenv("COST_MAX_MS", "20000"))

# Chars per token. Crude, but consistent - and consistency is all the
# limiter needs. It must rank cheap vs expensive correctly, not predict
# milliseconds perfectly.
CHARS_PER_TOKEN = 4.0


def estimate_input_tokens(text: str) -> float:
    return max(1.0, len(text) / CHARS_PER_TOKEN)


def estimate_cost_ms(text: str, max_new_tokens: int) -> float:
    """Estimated model milliseconds this request will consume."""
    cost = (
        COST_C0
        + COST_C_IN * estimate_input_tokens(text)
        + COST_C_OUT * max(1, max_new_tokens)
    )
    return min(cost, COST_MAX_MS)


def describe() -> dict:
    return {
        "formula": "c0 + c_in*input_tokens + c_out*max_new_tokens",
        "c0": COST_C0,
        "c_in": COST_C_IN,
        "c_out": COST_C_OUT,
        "unit": "estimated_model_ms",
        "cap_ms": COST_MAX_MS,
    }
