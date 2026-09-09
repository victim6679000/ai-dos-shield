# Role Guide 3 — Mitigation Gateway & Integration

**Person C.** Read `00_SHARED_CONTRACT.md` first. This guide is yours alone.

> **Your role in one sentence**
> You own admission control. Every protected request reaches your gateway
> first: decide whether it may consume scarce inference capacity, keep
> waiting bounded, forward what you admit, and tighten automatically when
> pressure rises.

**You own:** `shield/`, `docker-compose.yml`, `scripts/`, `dashboard/`, and
daily integration.
**Not your job:** knowing which traffic is "really" an attack. You never
see that label, and that is the point.

This is the largest component. **A finishes early and B has a lighter
Thursday — hand work to them rather than carrying it all.**

---

## Contract at a glance

Your `/infer` must be indistinguishable from the inference server's, so B
can switch targets by changing only a base URL.

```
429 = this client exceeded its budget or in-flight cap
503 = the system has no safe capacity left
```

Full contract: `00_SHARED_CONTRACT.md`.

---

## 1. What the gateway is doing

```
incoming request
      │
   [ validate ]──────────────── invalid ──► 422 / 413
      │
   [ identify client ]
      │
   [ estimate cost in model-ms ]
      │
   [ per-client budget? ]────── no ──────► 429
      │ yes
   [ in-flight cap? ]────────── no ──────► 429
      │ yes
   [ capacity + budget priority? ]─ no ──► 503
      │ yes
   [ await semaphore → forward → record ]
      ▼
   response
```

The core idea is **admission control**: protect scarce capacity *before*
expensive work starts. A fast rejection is healthier than letting three
hundred requests queue until they all time out together.

---

## 2. Build in this order

Do not skip ahead. Each step needs the previous one working before it can
be tuned meaningfully.

1. **Transparent proxy.** `/infer` → `httpx` → `inference:8000/infer`. No
   mitigation. Prove responses match direct calls exactly.
2. **Token bucket, `cost=1`.** Prove 429 fires under a burst.
3. **Semaphore + bounded queue.** Prove overload produces a fast 503 rather
   than an ever-growing wait.
4. **`/shield/status`** with mode, active, waiting, and all counters.
5. **Swap `cost=1` for A's calibrated cost model.**
6. **In-flight cap, then budget-priority shedding.**
7. **NORMAL/PROTECTION state machine with hysteresis.** Last.

---

## 3. The four mechanisms

The brief asks for at least two. We have four, and the fourth is what makes
it *auto*-mitigation.

### Mechanism 1 — cost-aware token bucket

```
bucket capacity   C   (burst allowance, in estimated model-ms)
refill rate       R   (sustained allowance, model-ms per second)
request cost      K   (from A's fitted model)

if tokens >= K:  allow, subtract K
else:            429 with Retry-After = (K - tokens) / R
```

Denominating budgets in **milliseconds of compute** rather than in requests
is the central idea of the whole project. A 512-token generation costs ~26×
a short one, because it does.

Guard every bucket mutation with an `asyncio.Lock`. Without it, concurrent
requests all read the same token count and all pass — the check-then-act
race, which silently defeats the limiter under exactly the concurrency you
are testing. Expire idle buckets so a client-rotating attacker cannot grow
your memory forever.

### Mechanism 2 — per-client in-flight cap

```python
MAX_INFLIGHT_PER_CLIENT = 2
```

A real user is closed-loop and essentially never has more than one request
outstanding. An attacker is open-loop and their in-flight count climbs as
the server slows. **That is a behavioural signal separating humans from
floods with no attack label and no ML.** Cheap, explainable, effective.

### Mechanism 3 — budget-priority load shedding

```python
required = max(0.0, occupancy - SHED_GRACE)
if at_capacity or client_remaining_budget < required:
    return 503
```

When the queue fills, someone must be turned away. First-come-first-served
sheds whoever arrives next — under a flood that is mostly legitimate users.
Instead: **the deeper the queue, the fuller your bucket must be to get in.**
Light users sail through, heavy consumers are shed first. The client's own
consumption decides, not a label.

`NEW_CLIENT_FILL=0.35` makes unknown client IDs start partly filled. Without
it, minting a fresh ID per request defeats every per-client mechanism —
this took our sybil result from 61% to 98% legitimate success. The honest
trade-off, which belongs in the PDF: genuinely new users rank below
established ones while the system is under pressure.

### Mechanism 4 — automatic mode switching

```
NORMAL
  │  occupancy > 0.70  OR  p95 > threshold,  for 3 consecutive checks
  ▼
PROTECTION   (slower refill, smaller queue, tighter in-flight cap)
  │  occupancy < 0.30  AND  p95 recovered,   for 6 consecutive checks
  ▼
NORMAL
```

Two choices you must be able to defend:

- **Hysteresis** — enter high, exit low. A single threshold makes the mode
  flap every few seconds when pressure sits near the boundary.
- **Consecutive checks** — one unlucky slow request must not trigger
  protection; sustained pressure must.

Keep it two states with clear thresholds. A complex anomaly detector you
cannot explain or debug in three days will lose more marks than it gains.

---

## 4. Tuning without guessing

| Step | What it gives you |
|---|---|
| B's normal baseline | Typical legitimate p95 and active count |
| B's incremental ramp | The undefended saturation point |
| Set `MAX_ACTIVE` | Just past best throughput, before p95 explodes |
| Set `MAX_WAITING` | Absorbs a burst, prevents a long queue |
| Set `BUCKET_REFILL_MS_PER_S` | **Above** measured legitimate demand, below what an attacker needs |
| Set controller thresholds | Trigger before legitimate p95 collapses, without flapping |

> **The trap we actually fell into.** Protection mode cut the refill rate to
> 50% and multiplied costs by 1.5. Legitimate p95 improved 10× and the chart
> looked magnificent — but legitimate success fell from 95% to **64%**. The
> defence was throttling real users.
>
> Compute what a legitimate client consumes per second and set the sustained
> budget comfortably above it, *including* after protection scaling. Ask B
> for the success-rate column on every run, not just p95.

**Never tune against the attack label.** If you catch yourself wanting to
know which clients B marked as attackers, stop. Tune on observable pressure
and per-client consumption only.

---

## 5. Implementation details that bite

| Bug | Symptom | Prevention |
|---|---|---|
| Bucket race | Client gets more budget than configured under load | `asyncio.Lock` around every mutation |
| Semaphore not released | Capacity permanently shrinks after an error | Acquire/release via `async with`, or `finally` |
| Waiting counter leak | Permanent 503s after one timeout | Decrement in `finally`; test with the upstream killed |
| New `httpx` client per request | Connection setup pollutes every latency measurement | One shared `AsyncClient` |
| No upstream timeout | A dead inference server pins gateway workers forever | Explicit connect and read timeouts |
| Threshold flapping | Mode toggles every few seconds | Hysteresis + consecutive checks |
| Over-aggressive limiter | Legitimate users get 429 at normal load | Calibrate against baseline demand |
| Unbounded state dict | Memory grows with fake client IDs | Expire idle buckets |

---

## 6. You are also integration lead

**Every day, from a clean pull:**

```bash
docker compose up --build
curl localhost:8000/health && curl localhost:8080/health
# one /infer direct, one through the shield
python scripts/edge_cases.py --target http://localhost:8080
```

### Friday hardening — the rubric tests this explicitly

*"Does the code run end to end from the README, does the scenario complete,
and does it survive a second run and unexpected input."*

- [ ] Clean clone on a machine that has never seen the repo
- [ ] Full suite runs **twice in a row** with no manual cleanup — no port
      conflicts, no stale state, no leaked counters
- [ ] `scripts/edge_cases.py` passes: missing client ID, empty text,
      negative and absurd token counts, oversized body, wrong field types,
      malformed JSON, unicode, very long client ID
- [ ] Inference killed mid-run → clean 502/504, no hang, recovery when it
      returns
- [ ] Shield restarted mid-run → counters reset cleanly
- [ ] README instructions followed literally by someone who did not write them

### The demo

`dashboard/index.html` shows mode, queue depth and counters live, and pulses
red in PROTECTION. It is what makes the auto-mitigation visible on stage and
it screenshots straight into the PDF.

**Record a backup run.** Live load on a laptop is variable and a failed live
demo is worth zero. Have the recording and the charts ready.

---

## 7. Definition of done

- [ ] Shield forwards valid `/infer` transparently, response contract preserved
- [ ] Token bucket returns 429 correctly and is safe under concurrency
- [ ] In-flight cap enforced per client
- [ ] Active capped, waiting bounded, overflow returns fast 503
- [ ] Cost-aware credits distinguish cheap from expensive work
- [ ] NORMAL/PROTECTION switches automatically with hysteresis
- [ ] `/shield/status` reports mode, active/waiting, all counters, p50/p95,
      and mode transitions with timestamps
- [ ] The shield has never seen the ground-truth label
- [ ] **Legitimate success rate stays high while p95 improves** — both, not one
- [ ] B shows measurable improvement on at least `flood` and
      `expensive_low_rate`
- [ ] Clean checkout passes the integrated smoke test twice in a row

---

## 8. Judge Q&A — you are the team's specialist here

**Why per-client rather than a global limit?**
A global limit can be monopolised by one abusive client. Per-client budgets
preserve fairness so a single identity cannot consume the whole allowance.

**Why a token bucket?**
Refill rate controls sustained consumption, bucket capacity controls burst
size. It lets a legitimate user burst briefly while still bounding a
sustained abuser.

**Why is your limiter cost-aware?**
AI requests vary enormously in compute. Counting one 512-token generation
the same as one 8-token request lets low-rate expensive traffic walk
straight through a request-count limiter. Our budgets are denominated in
milliseconds of model time, fitted from real measurements with an R² of
[A's number].

**Why reject rather than queue?**
Once safe capacity is gone, a long queue just converts one problem into
widespread timeouts and memory growth. Fast shedding preserves service for
work the system can actually complete.

**429 versus 503?**
429 is client-specific — that client exhausted its own budget. 503 is
system-wide — no safe capacity remains for anyone.

**What makes this auto-mitigation rather than a switch?**
The gateway samples its own queue occupancy and recent tail latency and
switches into a stricter mode on sustained pressure, then relaxes after a
longer stable recovery window. You can watch it happen on the dashboard.

**Why hysteresis?**
A higher entry threshold than exit threshold stops the mode oscillating when
the metric sits near a boundary.

**Couldn't an attacker rotate client IDs to bypass per-client limits?**
Yes, and initially they did — our sybil profile defeated the defence
entirely. We mitigated it by giving unknown client IDs partial starting
budget, so rotation stops paying. It is a mitigation, not a solution: a
patient attacker who ages identities would still get through. In production
you bind budgets to authenticated API keys.

**Isn't trusting `X-Client-ID` unrealistic?**
Completely, and we say so. It is a test identity for a controlled
experiment. Production identity comes from authentication, never from a
client-supplied header.

**How do you know you aren't just blocking everyone?**
Because we measure legitimate success rate separately and report it beside
p95. An early configuration of ours improved p95 tenfold while dropping
legitimate success to 64% — we caught it, and fixed it, precisely because we
plot both.
