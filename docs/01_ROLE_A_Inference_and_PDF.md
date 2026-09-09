# Role Guide 1 — AI Inference Server & Case PDF

**Person A.** Read `00_SHARED_CONTRACT.md` first. This guide is yours alone.

> **Your role in one sentence**
> You own the target service — expose a real model through a stable HTTP
> API, make its cost measurable — and from Wednesday afternoon you own the
> Case PDF, which carries 35% of the marks.

**You own:** `inference/`, `analysis/calibrate.py`, `docs/CASE_PDF.md`
**Not your job:** generating attack traffic, deciding what gets rejected

---

## Contract at a glance

```http
POST /infer     {"text": "...", "max_new_tokens": 16}
  -> 200        {"request_id": "...", "output": "...", "model_ms": 243.8}
GET  /health    -> {"status":"ok","model_ready":true}   (never runs inference)
```

Full contract and status-code meanings: `00_SHARED_CONTRACT.md`.

---

## 1. Beginner lesson — what an inference server is

**Inference** means using an already-trained model to produce an output.
You are not training anything. You are packaging an existing small model
behind an API.

```
request → FastAPI → tokenizer → model forward pass → text → response
```

- **Tokenizer** turns text into the numeric tokens the model consumes.
- **Forward pass** is the actual computation. This is what costs money.
- **`model_ms`** is time spent *inside* the model. It excludes queueing and
  network. That separation is what lets the team prove where latency comes
  from.

### Why this matters for the project

Web requests are roughly equal-cost. **AI requests are not.** A request
asking for 512 output tokens costs dramatically more than one asking for 8,
because the model generates tokens one at a time — the cost is close to
linear in `max_new_tokens`.

This is the entire premise of our project. "5 requests per second" tells you
almost nothing about the load on an inference server. You will be the person
who proves that with measurements.

---

## 2. What to build

| Requirement | Minimum implementation | Why |
|---|---|---|
| `POST /infer` | Accept text + `max_new_tokens`, run model, return `output` + `model_ms` | The target endpoint |
| `GET /health` | 200 + `model_ready` | Docker healthcheck and team smoke tests |
| Load model once | At startup, global singleton | Loading per request destroys every measurement |
| Bounded workers | `ThreadPoolExecutor(INFER_WORKERS)` | This is the scarce capacity the shield protects |
| Observability | total / active / success / fail / recent `model_ms` | The team's evidence of degradation |
| Validation | Reject oversized text with 413 **before** model work | An absurd request must not consume a worker |
| Config | Everything in `.env` | Nothing hard-coded |

### Keep it undefended

Do **not** put rate limiting, queueing, or shedding in the inference
server. If the target defends itself there is nothing left to measure, and
the before/after comparison collapses. Your service is the patient, not the
doctor.

### The mock mode is not a shortcut

`MOCK_MODEL=true` replaces the forward pass with a calibrated sleep. It runs
through the *same* executor, so concurrency and queueing behave identically.

Ship this on Tuesday night, before the model works. It unblocks B and C
immediately. Keep it afterwards as the demo fallback — a laptop that decides
to be slow at the wrong moment should not end the presentation. If you use
it live, say so.

---

## 3. Implementation order

1. FastAPI app with `/health` and a fake `/infer` that sleeps 200 ms.
   **Commit within the first hour.**
2. Load the real model at startup. Never inside the route handler.
3. Warm up with 3+ requests during startup.
4. Measure `model_ms` with `time.perf_counter` around the model call only.
5. Active counter: increment before, decrement in `finally`.
6. `/metrics` endpoint. Keep it cheap.
7. Dockerfile. **CPU-only torch** — the default wheel pulls ~2 GB of CUDA
   libraries you cannot use:
   ```dockerfile
   RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1
   ```

### Model choice on a CPU laptop

`google/flan-t5-small` (~300 MB) is real text generation, small enough to be
reproducible, and `max_new_tokens` gives B a clean lever for request cost.

Set `TORCH_THREADS=2` and `INFER_WORKERS=2`. Small numbers make saturation
reachable at safe request rates and keep runs repeatable. Do not let torch
grab every core — the load generator is on the same machine and will
contend with it.

---

## 4. Wednesday morning: the calibration sweep

This is your most valuable technical contribution.

```bash
python analysis/calibrate.py --target http://localhost:8000
```

It sweeps input length × `max_new_tokens`, then fits by least squares:

```
model_ms ≈ c0 + c_in · input_tokens + c_out · max_new_tokens
```

It prints the coefficients, the **R²**, and the cheap-vs-expensive cost
ratio. The reference-laptop run completed on 9 September 2026 with R² =
**0.8912** and an **8.4x** modelled cost ratio. Its coefficients are in
`.env.example`, and the full 48-observation record is in
`results/raw/calibration.json`. Give those results to A-as-PDF-author (you)
and to C.

Why this matters: without it, C's limiter charges clients in made-up
credits. With it, the limiter charges in **measured milliseconds of
compute**. That converts a hand-waved formula into a fitted model with a
reported goodness of fit — which is exactly what "statistics applied
correctly" in the 25% technical-depth criterion is looking for.

Run it on the **final demo laptop**, not yours, if they differ.

---

## 5. From Wednesday afternoon: the Case PDF

Freeze the inference server. Resist optimising it — a performance change
after Thursday's freeze invalidates B's numbers.

Your job is now `docs/CASE_PDF.md`: five pages, five required sections. It
is worth more marks than any single service in the repo. Gather from the
team as you go rather than asking for everything on Friday:

- From B: the headline p95 and success-rate numbers, and the charts
- From C: the mechanism list and the honest limitations
- From yourself: the cost model, the R², the cost ratio

---

## 6. Common failure modes

| Problem | Cause | Fix |
|---|---|---|
| First request wildly slow | Lazy model/tokenizer init | Warm up at startup; never include warm-up in results |
| Every request is slow | Model constructed inside the route | Load once as a global singleton |
| Active count drifts upward | Decrement skipped on error paths | Decrement in `finally`, always |
| Laptop freezes under load | Too many workers, torch grabbing all cores | `INFER_WORKERS=2`, `TORCH_THREADS=2` |
| Shield looks faster than direct | Model not warmed equally, or config changed between runs | Freeze config, warm both |
| `model_ms` ≠ end-to-end latency | Queueing and network exist | That gap **is** the finding. Explain it, don't hide it. |

---

## 7. Definition of done

- [x] `POST /infer` matches the contract and returns a real model result
- [x] `GET /health` returns 200 reliably once the model is ready
- [x] Model loaded exactly once; server survives controlled overload
- [x] Every response carries `request_id` and `model_ms`
- [x] Active/total counters never leak, verified after a failed run
- [x] Oversized input returns 413 before any model work
- [x] Calibration run on the demo laptop, coefficients in `.env`, R² recorded
- [x] `MOCK_MODEL=true` and `false` both work and give the same API
- [ ] B has run normal and overload traffic directly against it
- [ ] C has forwarded traffic through the shield to it
- [ ] `docs/CASE_PDF.md` complete, exported ≤5 pages and ≤20 MiB

---

## 8. Judge Q&A — you are the team's specialist here

**What is inference?**
Using an already-trained model to produce an output for new input. We are
not training anything during this demo.

**Why is an inference endpoint different from a normal web endpoint?**
A single request can trigger seconds of dedicated compute, and the cost
varies with input and output length. A trivial number of requests can
consume an enormous amount of capacity — which is why request-count rate
limiting is the wrong tool.

**Why this model?**
It is real text generation, small enough to run reproducibly on a CPU
laptop, and `max_new_tokens` gives us a clean, honest lever for varying
request cost.

**Where do your cost coefficients come from?**
A calibration sweep against the real model — sixteen configurations of input
length by output length with three repeats each, fitted by least squares. The
reference run produced R² = 0.8912 and an 8.4x modelled cost range. They are
measured, not assumed.

**Why does p95 rise under overload but the average stay tolerable?**
As arrival rate approaches capacity, queueing delay grows non-linearly and
hits the tail first. An average hides a minority of users having an
unusable experience; p95 exposes it.

**Why not just add more servers?**
Autoscaling is real but it is not instantaneous, it has a cost ceiling, and
against cost-based abuse it scales your bill rather than solving the
problem. Admission control is what protects finite inference capacity.

**Did you use the mock mode in this demo?**
Both modes were tested. The calibration used the real model; mock mode
replaces only the forward pass with a timing model fitted to those real
measurements and runs through the same concurrency path. For the final demo,
state honestly which mode produced the numbers being shown.
The real model is one environment variable away.
