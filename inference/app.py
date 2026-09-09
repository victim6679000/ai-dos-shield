"""
AI Inference Server - the target service.

This is deliberately UNPROTECTED. All admission control lives in the shield.
Its only jobs are: run the model, measure honestly, expose counters.

Two modes, selected by MOCK_MODEL:
  MOCK_MODEL=true   -> no torch needed, sleeps for a calibrated duration.
                       Use this on day 1 and as a demo fallback.
  MOCK_MODEL=false  -> real Hugging Face model.

Both modes go through the SAME bounded executor, so concurrency and
queueing behave identically. That is what makes the mock a legitimate
stand-in rather than a cheat.
"""

import asyncio
import logging
import math
import os
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from threading import Lock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# Configuration - every tunable lives here, nothing hard-coded downstream
# ----------------------------------------------------------------------
MOCK_MODEL = os.getenv("MOCK_MODEL", "true").lower() == "true"
MODEL_NAME = os.getenv("MODEL_NAME", "google/flan-t5-small")
INFER_WORKERS = int(os.getenv("INFER_WORKERS", "2"))
TORCH_THREADS = int(os.getenv("TORCH_THREADS", "2"))
MAX_NEW_TOKENS_CAP = int(os.getenv("MAX_NEW_TOKENS_CAP", "256"))
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "8000"))
MAX_INPUT_TOKENS = int(os.getenv("MAX_INPUT_TOKENS", "512"))
MODEL_WARMUP_REQUESTS = int(os.getenv("MODEL_WARMUP_REQUESTS", "3"))
METRICS_WINDOW_SIZE = int(os.getenv("METRICS_WINDOW_SIZE", "200"))

# Mock timing model. Replace these with numbers measured from YOUR laptop
# (analysis/calibrate.py prints the exact line to paste here).
MOCK_BASE_MS = float(os.getenv("MOCK_BASE_MS", "60"))
MOCK_MS_PER_OUT_TOKEN = float(os.getenv("MOCK_MS_PER_OUT_TOKEN", "18"))
MOCK_MS_PER_IN_TOKEN = float(os.getenv("MOCK_MS_PER_IN_TOKEN", "0.8"))
MOCK_JITTER = float(os.getenv("MOCK_JITTER", "0.10"))
MOCK_CHARS_PER_TOKEN = float(os.getenv("MOCK_CHARS_PER_TOKEN", "4.0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("inference")

# ----------------------------------------------------------------------
# Mutable state
# ----------------------------------------------------------------------
_state_lock = Lock()
_active = 0
_total = 0
_ok = 0
_failed = 0
_recent_model_ms: list[float] = []

_model = None
_tokenizer = None
_model_ready = False
_executor: ThreadPoolExecutor | None = None


class InferRequest(BaseModel):
    text: str = Field(..., min_length=1)
    max_new_tokens: int = Field(16, ge=1)


def _load_model() -> None:
    """Load once at startup. NEVER inside a request handler."""
    global _model, _tokenizer, _model_ready

    if MOCK_MODEL:
        log.info("MOCK_MODEL=true - skipping model load, using timing simulation")
        _model_ready = True
        return

    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    torch.set_num_threads(TORCH_THREADS)
    log.info("loading %s (torch threads=%d)...", MODEL_NAME, TORCH_THREADS)
    _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    _model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME)
    _model.eval()

    # Warm up. The first forward pass is always an outlier and must never
    # appear in benchmark results.
    for _ in range(MODEL_WARMUP_REQUESTS):
        _run_real("warm up", 8)

    _model_ready = True
    log.info("model ready")


def _run_real(text: str, max_new_tokens: int) -> tuple[str, float, int]:
    """Return decoded output, generation-only milliseconds, and input tokens."""
    import torch

    # Tokenization is intentionally outside the timer. model_ms represents the
    # scarce model-generation work, not parsing or response formatting.
    inputs = _tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=MAX_INPUT_TOKENS,
    )
    input_tokens = int(inputs["input_ids"].shape[-1])
    with torch.no_grad():
        t0 = time.perf_counter()
        out = _model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        model_ms = (time.perf_counter() - t0) * 1000.0

    # Decoding is also outside the timer for the same reason.
    output = _tokenizer.decode(out[0], skip_special_tokens=True)
    return output, model_ms, input_tokens


def _run_mock(text: str, max_new_tokens: int) -> tuple[str, float, int]:
    """Simulate only the model-generation portion of a real request."""
    input_tokens = min(
        MAX_INPUT_TOKENS,
        max(1, math.ceil(len(text) / MOCK_CHARS_PER_TOKEN)),
    )
    ms = (
        MOCK_BASE_MS
        + MOCK_MS_PER_OUT_TOKEN * max_new_tokens
        + MOCK_MS_PER_IN_TOKEN * input_tokens
    )
    ms *= 1.0 + random.uniform(-MOCK_JITTER, MOCK_JITTER)

    t0 = time.perf_counter()
    time.sleep(ms / 1000.0)
    model_ms = (time.perf_counter() - t0) * 1000.0
    return f"[mock output for {max_new_tokens} tokens]", model_ms, input_tokens


def _blocking_infer(text: str, max_new_tokens: int) -> tuple[str, float, int]:
    """Run the selected forward pass inside the bounded executor thread."""
    return _run_mock(text, max_new_tokens) if MOCK_MODEL else _run_real(text, max_new_tokens)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _executor
    _executor = ThreadPoolExecutor(max_workers=INFER_WORKERS)
    await asyncio.get_running_loop().run_in_executor(None, _load_model)
    yield
    _executor.shutdown(wait=False)


app = FastAPI(title="AI Inference Server", lifespan=lifespan)


@app.get("/health")
def health():
    """Must stay cheap. Never runs inference."""
    return {
        "status": "ok",
        "model_ready": _model_ready,
        "mock": MOCK_MODEL,
        "model": MODEL_NAME,
        "workers": INFER_WORKERS,
        "torch_threads": TORCH_THREADS,
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_new_tokens_cap": MAX_NEW_TOKENS_CAP,
    }


@app.get("/metrics")
def metrics():
    with _state_lock:
        recent = list(_recent_model_ms)
        return {
            "active_requests": _active,
            "request_count": _total,
            "success": _ok,
            "failed": _failed,
            "recent_model_ms_avg": round(sum(recent) / len(recent), 2) if recent else None,
            "workers": INFER_WORKERS,
        }


@app.post("/infer")
async def infer(req: InferRequest):
    global _active, _total, _ok, _failed

    # Validation happens BEFORE any expensive work. An absurd request
    # should fail instantly, not consume a worker.
    if len(req.text) > MAX_TEXT_CHARS:
        return JSONResponse(
            status_code=413,
            content={"error": "text_too_long", "max_chars": MAX_TEXT_CHARS},
        )
    max_new_tokens = min(req.max_new_tokens, MAX_NEW_TOKENS_CAP)

    request_id = uuid.uuid4().hex[:12]

    with _state_lock:
        _active += 1
        _total += 1

    try:
        loop = asyncio.get_running_loop()
        output, model_ms, input_tokens = await loop.run_in_executor(
            _executor, _blocking_infer, req.text, max_new_tokens
        )
        with _state_lock:
            _ok += 1
            _recent_model_ms.append(model_ms)
            if len(_recent_model_ms) > METRICS_WINDOW_SIZE:
                del _recent_model_ms[:-METRICS_WINDOW_SIZE]
        return {
            "request_id": request_id,
            "output": output,
            "model_ms": round(model_ms, 2),
            "input_tokens": input_tokens,
        }

    except Exception as exc:
        log.exception("inference failed")
        with _state_lock:
            _failed += 1
        return JSONResponse(
            status_code=500,
            content={"error": "inference_failed", "detail": str(exc)[:200], "request_id": request_id},
        )
    finally:
        # finally, always. A leaked counter here poisons every later run.
        with _state_lock:
            _active -= 1
