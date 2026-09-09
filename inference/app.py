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

# Mock timing model. Replace these with numbers measured from YOUR laptop
# (analysis/calibrate.py prints the exact line to paste here).
MOCK_BASE_MS = float(os.getenv("MOCK_BASE_MS", "60"))
MOCK_MS_PER_OUT_TOKEN = float(os.getenv("MOCK_MS_PER_OUT_TOKEN", "18"))
MOCK_MS_PER_IN_TOKEN = float(os.getenv("MOCK_MS_PER_IN_TOKEN", "0.8"))
MOCK_JITTER = float(os.getenv("MOCK_JITTER", "0.10"))

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
    for _ in range(3):
        _run_real("warm up", 8)

    _model_ready = True
    log.info("model ready")


def _run_real(text: str, max_new_tokens: int) -> str:
    import torch

    inputs = _tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        out = _model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    return _tokenizer.decode(out[0], skip_special_tokens=True)


def _run_mock(text: str, max_new_tokens: int) -> str:
    est_in = max(1, len(text) / 4)
    ms = (
        MOCK_BASE_MS
        + MOCK_MS_PER_OUT_TOKEN * max_new_tokens
        + MOCK_MS_PER_IN_TOKEN * est_in
    )
    ms *= 1.0 + random.uniform(-MOCK_JITTER, MOCK_JITTER)
    time.sleep(ms / 1000.0)
    return f"[mock output for {max_new_tokens} tokens]"


def _blocking_infer(text: str, max_new_tokens: int) -> tuple[str, float]:
    """Runs in the executor thread. Returns (output, model_ms)."""
    t0 = time.perf_counter()
    output = _run_mock(text, max_new_tokens) if MOCK_MODEL else _run_real(text, max_new_tokens)
    return output, (time.perf_counter() - t0) * 1000.0


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
    return {"status": "ok", "model_ready": _model_ready, "mock": MOCK_MODEL}


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
        output, model_ms = await loop.run_in_executor(
            _executor, _blocking_infer, req.text, max_new_tokens
        )
        with _state_lock:
            _ok += 1
            _recent_model_ms.append(model_ms)
            if len(_recent_model_ms) > 200:
                del _recent_model_ms[:-200]
        return {"request_id": request_id, "output": output, "model_ms": round(model_ms, 2)}

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
