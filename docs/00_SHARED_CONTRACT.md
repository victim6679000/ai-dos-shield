# 00 — Shared Contract & Schedule

**Everyone reads this once. Your role guide is 01, 02, or 03.**

This is the only copy of the contract. If it changes, it changes here, and
whoever changes it announces it before writing any code. Three copies of a
contract means three copies that quietly drift apart.

---

## Deadline

**11 September, 23:59 GST. We upload at 18:00 GST.**

Only the captain can upload. Six hours of buffer is not padding — it is
what covers the export that breaks, the file that is 21 MiB, and the
antivirus scan that takes longer than expected.

---

## How the marks are actually awarded

| Criterion | Weight | Primary owner |
|---|---|---|
| Fit to brief & business problem | 20% | A (Case PDF) |
| Relevance of the solution | 15% | A (Case PDF) |
| Does the prototype work | 25% | C (integration) |
| Technical depth & correctness | 25% | All three |
| Innovation | 15% | A writes it, B and C build it |

**35% is carried by a document, not by code.** Three excellent components
plus a PDF written at midnight on the last day scores badly. Person A owns
the PDF from Wednesday afternoon and stops touching code.

---

## System boundaries

```
TEST A (baseline)    loadgen ─────────────────────────► inference :8000
TEST B (protected)   loadgen ──► shield :8080 ────────► inference :8000
```

The **only** difference between the two runs is whether traffic passes the
shield. Same model, same machine, same duration, same seed, same prompt
corpus, same client mix. Anything else varying invalidates the comparison,
and a judge will ask.

---

## Frozen HTTP contract

Both `inference:8000` and `shield:8080` expose the same endpoint.

```http
POST /infer
X-Client-ID: client-001
Content-Type: application/json

{"text": "Explain why caching helps performance.", "max_new_tokens": 16}
```

Success:

```json
{"request_id": "8f7a12bc", "output": "...", "model_ms": 243.8}
```

Status codes — these meanings are fixed:

| Code | Meaning |
|---|---|
| 200 | Served |
| 413 | Request body too large (rejected before any model work) |
| 422 | Malformed or invalid fields |
| **429** | **This client** exceeded its cost budget or in-flight cap |
| **503** | **The system** has no safe capacity left |
| 502 / 504 | Upstream inference unavailable or timed out |

Never blur 429 and 503. Judges ask about exactly this distinction, and the
answer is the clearest evidence that the defence is deliberate rather than
accidental.

Both services expose a cheap `GET /health`. It never runs inference.
The shield additionally exposes `GET /shield/status`.

New response fields must be **additive**. Never rename or repurpose one.

---

## The integrity rule

The load generator labels every simulated client `legitimate` or `attack`.

**That label is written to `requests.csv` and never sent over the wire.**

The shield decides using observable signals only: estimated request cost,
the client's remaining budget, its in-flight count, and queue occupancy. It
has no way to know which clients B designated as attackers.

If at any point you find yourself wanting the shield to know the label —
stop. A defence tuned against a secret label proves nothing, and a judge
who spots it will discount the entire result.

---

## Repository layout

```
ai-dos-shield/
├── inference/     A owns
├── shield/        C owns
├── loadgen/       B owns
├── analysis/      calibrate.py = A,  make_charts.py = B
├── scripts/       C owns
├── dashboard/     C owns
├── docs/          00 contract, 01/02/03 role guides, CASE_PDF.md
├── results/raw/   run outputs, committed so results are auditable
├── docker-compose.yml
├── .env.example   every tunable lives here. No magic numbers in code.
└── README.md
```

One repo, feature branches, small pull requests. `main` is always the last
known-working integrated version. Do not rewrite another person's component
without asking.

---

## Schedule

### Tuesday evening — 3 hours, all three on a call

- [ ] Repo up, `cp .env.example .env`, `docker compose up --build` works
      with mock inference and a pass-through shield
- [ ] This contract read aloud and agreed
- [ ] Captain confirmed
- [ ] Pitch sentence agreed and written into the README

Docker works **tonight**. Docker discovered on day three is the single most
common way hackathon teams lose.

### Wednesday — make it real
- A: real model loaded once, `model_ms`, warm-up, active counter
- B: five profiles emitting CSV, target as a config switch
- C: proxy, token bucket, semaphore, bounded queue, `/shield/status`
- **End of day: one ugly before/after run exists.** Ugly is acceptable.
  Missing is not.

### Thursday — make it good
- Morning: A runs `analysis/calibrate.py`, pastes coefficients into `.env`,
  records the R²
- Midday: C adds the in-flight cap, budget-priority shedding, then the
  auto-mode state machine. B finds the saturation point and helps tune
  `MAX_ACTIVE` / `MAX_WAITING`.
- **15:00 — freeze all config.** Everything after this is measurement, not
  tuning.
- Afternoon: final runs, all five profiles, both targets. Charts built.
- Evening: PDF draft complete with real numbers in it.

### Friday — make it safe
- **12:00 code freeze.** Bug fixes only.
- Clean-clone test on a machine that has never seen the repo
- Run the whole suite **twice in a row** — the rubric tests this explicitly
- `scripts/edge_cases.py` passes
- PDF exported, page count and file size checked
- **18:00 — captain uploads**

### Every day
- 15 minutes at the start: what works, what is blocked, what interface
  change is proposed
- End of day, C pulls `main` clean and verifies both paths

---

## Rules for fair experiments

1. Load testing targets `localhost` only. This is an authorized stress test
   of our own service.
2. Same model, machine, duration, seed, corpus and client mix for BEFORE
   and AFTER.
3. Warm the model before measuring. The first forward pass is always an
   outlier and must never enter results.
4. Record the configuration for every run — `config.json` does this
   automatically, do not bypass it.
5. Never silently change the attack between runs to improve a chart.
   Reproducibility is part of what is being graded.
6. If a run produces an odd result, report it. Do not delete it and re-run
   until the graph looks nice.
