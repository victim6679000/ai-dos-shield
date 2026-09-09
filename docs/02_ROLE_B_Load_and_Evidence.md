# Role Guide 2 — Load Generation & Evidence

**Person B.** Read `00_SHARED_CONTRACT.md` first. This guide is yours alone.

> **Your role in one sentence**
> You own the experiment. Build realistic repeatable traffic, keep the
> ground-truth labels in your own results and nowhere else, and prove with
> numbers that the service fails without protection and serves legitimate
> users with it.

**You own:** `loadgen/`, `analysis/make_charts.py`, `results/`
**Not your job:** the model, or deciding what the shield rejects

---

## Contract at a glance

```http
POST /infer     {"text": "...", "max_new_tokens": 16}
X-Client-ID: client-001          # neutral. Never "legit" or "attack".
```

429 = that client overspent. 503 = the system is full. Both are *defence
working*, not application failure. Full contract: `00_SHARED_CONTRACT.md`.

---

## 1. Beginner lesson — load testing vs DoS

Load testing sends traffic to a service **we own** to find its limits. A
denial-of-service attack uses traffic or expensive work to make a service
unavailable to real users. We simulate hostile patterns inside our own test
environment only.

```
arrival rate  <  capacity   →  queue stays small, latency stable
arrival rate  ≈  capacity   →  tail latency rises sharply
arrival rate  >  capacity   →  queue grows, timeouts and errors appear
```

| Term | Meaning |
|---|---|
| RPS | Requests sent per second |
| Concurrency | Requests in flight at the same time |
| Throughput | Requests **successfully completed** per second |
| p50 | Median latency |
| **p95** | 95% are faster. Exposes the slow tail. **Our headline metric.** |
| Legitimate success rate | Fraction of real-user requests getting a 2xx |
| Attack rejection rate | Fraction of abusive requests getting 429/503 |

### Closed-loop vs open-loop — the distinction that matters

A **real user is closed-loop**: send a request, wait for the answer, think,
send another. They essentially never have more than one request outstanding.
When the server slows down, they naturally send *less*.

An **attacker is open-loop**: fire at a fixed rate whether or not the server
is coping. As the server slows, their outstanding count climbs without limit.

Model legitimate users closed-loop and attackers open-loop. This is not
cosmetic — C's in-flight cap exploits exactly this asymmetry, and if you
model attackers closed-loop by accident the defence will look useless.

---

## 2. The five profiles

| Profile | Behaviour | What it proves |
|---|---|---|
| `normal` | 4 closed-loop users, short prompts, human think time | Healthy baseline, legitimate p95 |
| `spike` | Baseline + a 15 s burst at high RPS | Can protection absorb a burst? |
| `flood` | Baseline + sustained high rate from many clients | Behaviour above capacity |
| `expensive_low_rate` | 3 clients, **2 rps**, long input, 256 output tokens | Request count is a poor proxy for compute |
| `sybil` | Flood with a **fresh client ID every request** | Why per-client limiting alone is not enough |

### On "low and slow"

The brief asks for a *"slow / low-and-slow attacker."* Classically that means
Slowloris — slow sockets held open. On an inference server the more
interesting version is **cheap request, expensive work**: two requests per
second asking for maximum-length generation.

Cover the brief literally *and* make the reframing an explicit talking
point: *"the AI-native form of low-and-slow isn't a slow socket, it's a
cheap request asking for expensive work."* That satisfies fit-to-brief and
demonstrates original thinking in the same sentence.

### The sybil profile is not optional

It is designed to **break our own defence**, and initially it did — success
rate stayed at 61% until C added new-client budget warm-up. Deliberately
attacking your own system and reporting where it bends is the most credible
thing on the whole submission. Keep it.

---

## 3. Rules you must not break

1. **Legitimate traffic runs in every profile, including during attacks.**
   With attacker traffic alone you cannot prove availability was preserved.
   This is the most common fatal mistake.
2. **Neutral client IDs.** `client-001`, `atk-003`, `sybil-00042`. Never send
   anything the shield could use to identify traffic class.
3. **Ground truth stays in the CSV.** The `ground_truth_class` column exists
   so *you* can compute legitimate p95 separately. It never crosses the wire.
4. **Fixed seed, fixed corpus.** BEFORE and AFTER must send identical
   traffic. Different traffic means the comparison is worthless.
5. **Warm up before measuring**, every run.
6. **Never report only the average.** Averages hide exactly the failure this
   project is about.

---

## 4. Experiment sequence

1. Warm the model.
2. `normal` direct. Record baseline p50/p95 and throughput.
3. Ramp load in small steps until you find where the **undefended** server
   starts degrading. That is the saturation point — give it to C, who needs
   it to set `MAX_ACTIVE` and `MAX_WAITING`.
4. Run each attack profile direct, legitimate traffic continuing throughout.
5. Run the identical profiles through the shield. Same duration, seed,
   client mix, corpus.
6. Repeat the important runs 2–3 times if time allows. Use the
   representative run and **note outliers rather than deleting them**.
7. Charts from saved CSV only.

### Calibrating the attack

Start relative, then tune to the actual laptop. If the undefended service
degrades around 8 rps, a flood at 12–20 rps is plenty. If it handles 40,
scale up. **The numbers are hardware-dependent; the methodology is not.**

If the laptop freezes you have gone too far. The goal is measurable
degradation you can chart, not a dead machine — a frozen laptop produces no
data and no demo.

---

## 5. What every run records

`run_experiment.py` writes three files per run into `results/raw/<run_id>/`:

- **`requests.csv`** — one row per request: `t_rel`, `client_id`,
  `ground_truth_class`, `text_len`, `max_new_tokens`, `status_code`,
  `latency_ms`, `model_ms`, `error`
- **`shield.csv`** — shield state sampled every 0.5 s: mode, active,
  waiting, occupancy, counters
- **`config.json`** — profile, target, duration, seed, full profile spec

Never bypass `config.json`. A chart nobody can reproduce is worth nothing on
Friday when a judge asks how you got it.

---

## 6. The metrics that matter

> **Headline: legitimate-user p95 latency during the attack.** The brief
> explicitly asks whether legitimate users still get served while the attack
> is happening. That is the number that answers it.

| Metric | How to read it |
|---|---|
| Legitimate p95 | Should explode undefended, stay near baseline with the shield |
| **Legitimate success rate** | Must stay high. A defence that blocks everyone is not a defence. |
| Attack rejection rate | High 429/503 for abusive work is good *if* the row above holds |
| Queue depth | Unbounded growth undefended, bounded with protection |
| Overall error rate | Supporting only — controlled 429s rise **while the defence works** |

**Report legitimate success and attacker rejection separately, always.** A
combined error rate makes a working defence look like a broken service.

### Watch for the defence being too aggressive

During development the shield cut legitimate success from 95% to 64% while
improving p95 beautifully. The p95 chart alone looked like a triumph. Only
the success-rate column revealed the defence was throttling real users.
**Always plot both.** If success drops when the shield is on, tell C
immediately — the sustained budget is set below real legitimate demand.

---

## 7. Charts

| Chart | Content |
|---|---|
| 1 | Legitimate p95 over time, attack window shaded, direct vs shielded overlaid |
| 2 | Bar pair: legitimate p95 **and** legitimate success rate, before vs after |
| 3 | Queue depth over time with the automatic PROTECTION window shaded |

`analysis/make_charts.py` builds all three from CSV and writes
`summary.json` with the exact numbers to quote in the PDF.

Units on every axis. Scenario name in every title. No decorative charts.

---

## 8. Common mistakes

| Mistake | Why it hurts | Instead |
|---|---|---|
| Attacker traffic only | Cannot show availability was preserved | Legitimate stream always on |
| Different load before vs after | Comparison is invalid | Same profile, seed, duration, machine |
| Only average latency | Hides the tail failure | p50 + p95, p95 as headline |
| Treating 429 as failure | 429 is successful shedding | Separate legitimate success from attacker rejection |
| Maximising RPS | Freezes the laptop, teaches nothing | Calibrate around saturation |
| Shield sees the label | Result is rigged and worthless | Neutral IDs, labels in CSV only |
| One lucky run | Judges will question it | Repeat key runs, log config |
| Deleting a bad run | Dishonest, and they will ask | Report it and explain |

---

## 9. Definition of done

- [ ] All five profiles run against direct or shielded by config switch
- [ ] Legitimate traffic runs continuously in every attack scenario
- [ ] Ground-truth labels appear only in `results/`, never in a request
- [ ] Every run saves `requests.csv`, `shield.csv`, `config.json`
- [ ] You can compute legitimate p50/p95, success rate, attack rejection,
      and a full status-code breakdown
- [ ] Three charts generated from real run data, all axes labelled
- [ ] The suite runs twice in a row with no manual cleanup
- [ ] A teammate can reproduce a full comparison from the README
- [ ] You have written the exact result sentence the team quotes on stage

---

## 10. Judge Q&A — you are the team's specialist here

**How do you know the mitigation caused the improvement?**
Same model, machine, corpus, duration, seed and client mix. The single
intentional variable is whether requests go direct or through the shield.
We also ran the shield in pass-through mode as a control, which shows the
proxy hop itself costs almost nothing.

**Why p95 rather than average?**
An average can look fine while a meaningful minority of users are waiting
ten seconds. Queueing damages the tail first, so p95 is where overload
actually shows up.

**Why is the expensive profile called low-rate?**
It sends *fewer* requests but each asks for far more work. It is the
cleanest demonstration that request count is a weak proxy for AI compute
demand.

**Aren't 429s just your service failing?**
No — a 429 is a deliberate signal that a client exceeded its allowance. We
report legitimate success separately precisely so this distinction is
visible. Legitimate users stayed near [your number]% successful while
abusive traffic was rejected.

**Could a legitimate traffic spike look like an attack?**
Yes, and we don't claim otherwise. The shield reacts to resource pressure
and per-client consumption, not to attacker attribution. Our `spike` profile
tests exactly this case.

**Is this a real DDoS?**
No. It is an authorized local stress test simulating DoS traffic patterns.
A real DDoS is distributed across many networks and is neither necessary nor
appropriate for this experiment.

**Did any run contradict your conclusion?**
[Have a real answer.] Our sybil profile initially defeated the defence, and
an early shield configuration improved p95 while cutting legitimate success
to 64%. Both are in the results and both drove design changes.
