# AI Inference Shield

**Cost-aware DoS mitigation for AI inference endpoints.**

Conventional rate limiters count requests. GPUs bill in tokens. A client
sending 3 requests/second at `max_new_tokens=512` sits comfortably inside a
100 req/s limit while consuming more compute than a hundred normal users.
This project builds that attack, then defends against it by pricing every
request in **estimated milliseconds of model time** instead of in requests.

Measured result on the reference laptop (sustained flood, 2 model workers):

| | No mitigation | Shielded |
|---|---|---|
| Legitimate p95 latency | 9,841 ms | **1,437 ms** |
| Legitimate success rate | 100% | **97.9%** |

Latency falls ~7x while legitimate users keep being served. Numbers
regenerate from `bash scripts/run_all.sh` — nothing here is hand-drawn.

---

## Quick start (2 minutes, no model download)

```bash
git clone https://github.com/victim6679000/ai-dos-shield.git && cd ai-dos-shield
cp .env.example .env
docker compose up --build
```

Then, in a second terminal:

```bash
pip install -r loadgen/requirements.txt

# smoke test
curl localhost:8080/health
curl -X POST localhost:8080/infer -H 'Content-Type: application/json' \
     -H 'X-Client-ID: client-001' \
     -d '{"text":"Explain why caching helps performance.","max_new_tokens":16}'

# the headline experiment: same traffic, with and without the shield
python loadgen/run_experiment.py --profile flood --target http://localhost:8000 --duration 60
python loadgen/run_experiment.py --profile flood --target http://localhost:8080 \
       --shield-url http://localhost:8080 --duration 60

python analysis/make_charts.py \
  --direct results/raw/<the_direct_run> --shield results/raw/<the_shield_run>
```

Open `dashboard/index.html` in a browser to watch the shield switch itself
into PROTECTION mode during the run.

**Everything above runs with `MOCK_MODEL=true`**, which simulates inference
timing instead of loading a model. To use the real model, set
`MOCK_MODEL=false` in `.env` and allow ~2 minutes for the first startup
(`google/flan-t5-small`, ~300 MB). The API, the shield, and every experiment
use the same required response fields and request path either way — the mock
only replaces the forward pass with calibrated timing.

### Without Docker

```bash
pip install -r inference/requirements.txt -r shield/requirements.txt
uvicorn app:app --app-dir inference --port 8000    # terminal 1
uvicorn app:app --app-dir shield    --port 8080    # terminal 2
```

---

## Architecture

```
TEST A (baseline)   loadgen ─────────────────────────► inference :8000
TEST B (protected)  loadgen ──► shield :8080 ────────► inference :8000
```

The only difference between the two runs is whether traffic passes the
shield. Same model, same machine, same duration, same seed, same prompt
corpus, same client mix. That is what makes the comparison mean anything.

```
inference/   the target service. Deliberately undefended.
shield/      the gateway. All admission control lives here.
loadgen/     traffic profiles + the experiment harness that writes evidence.
analysis/    cost-model calibration and chart generation.
scripts/     edge-case suite and the one-command full run.
dashboard/   live status page for the demo.
results/     raw CSVs and generated charts. Committed, so results are auditable.
```

## The four defence mechanisms

| # | Mechanism | Rejects with | Stops |
|---|---|---|---|
| 1 | Per-client token bucket priced in **model milliseconds** | 429 | Expensive low-rate abuse |
| 2 | Per-client outstanding-request cap | 429 | Open-loop flooding |
| 3 | Budget-priority load shedding on a bounded queue | 503 | Global overload |
| 4 | Automatic NORMAL ↔ PROTECTION switching with hysteresis | — | Makes it *auto*-mitigation |

**Why cost pricing.** `analysis/calibrate.py` sweeps input length against
output length on the real model and fits
`model_ms ≈ c0 + c_in·input_tokens + c_out·max_new_tokens` by least squares.
The real Docker run on the reference laptop produced R² = 0.8912 and a
modelled range from 731 ms to 6,147 ms across the measured grid — an 8.4x
cost ratio. Client budgets are denominated in those measured milliseconds.

**Why an outstanding-request cap.** A real user is closed-loop: send, wait,
think, send. They essentially never have more than one request in flight.
An attacker is open-loop and their in-flight count climbs as the server
slows. That is a behavioural signal separating humans from floods with no
attack label and no ML.

**Why budget-priority shedding.** When the queue fills, first-come-first-served
sheds whoever arrives next — under a flood, mostly legitimate users. Instead
the deeper the queue, the fuller your bucket must be to get in. Light users
sail through; heavy consumers are shed first.

**Why new clients start partly filled.** `NEW_CLIENT_FILL=0.35` means an
unknown client ID does not arrive with full standing. Without it, an
attacker minting a fresh ID per request defeats every per-client mechanism.
With it, identity rotation stops paying. The honest trade-off: genuinely new
users rank below established ones while the system is under pressure.

## Reproducing every number

```bash
bash scripts/run_all.sh 60      # all five profiles, direct + shielded, all charts
python scripts/edge_cases.py --target http://localhost:8080
python analysis/calibrate.py --target http://localhost:8000   # needs MOCK_MODEL=false
```

Each run writes `results/raw/<run_id>/` containing `requests.csv` (one row
per request, including the ground-truth label), `shield.csv` (status
sampled every 0.5 s), and `config.json` (everything needed to repeat it).

## Traffic profiles

| Profile | What it does | What it proves |
|---|---|---|
| `normal` | 4 closed-loop users | Healthy baseline |
| `spike` | Baseline + 15 s burst at 25 rps | Can protection absorb a burst? |
| `flood` | Baseline + sustained 20 rps from 12 clients | Behaviour above capacity |
| `expensive_low_rate` | 3 clients, **2 rps**, 256 output tokens | Request count is a poor proxy for compute |
| `sybil` | Flood with a fresh client ID every request | Why per-client limiting alone is not enough |

## Experimental integrity

The load generator labels each simulated client `legitimate` or `attack`.
**That label is written only to the CSV and is never sent over the wire.**
The shield decides using observable behaviour: request cost, client budget,
in-flight count, queue occupancy. A defence tuned against a secret label
would prove nothing.

`MITIGATION_ENABLED=false` runs the shield as a pass-through proxy. That is
the control experiment: it shows the proxy hop itself costs almost nothing,
so the measured improvement comes from admission control and not from an
accident of the network path.

## Known limitations

- **Identity rotation is mitigated, not solved.** New-client warm-up raises
  the cost of Sybil attacks but a patient attacker who ages identities
  defeats it. Production would bind budgets to authenticated API keys.
- **`X-Client-ID` is trusted.** Correct for a controlled experiment,
  never acceptable in production.
- **Single node.** This is application-layer admission control. It does not
  address volumetric network-layer DDoS, which belongs at the CDN.
- **Cost estimation is linear.** It ranks cheap vs expensive reliably, which
  is all admission control needs, but it does not model batching or KV-cache
  effects.

## Safety

All load generation targets `localhost` only. This is an authorized stress
test of our own service. Nothing here should be pointed at infrastructure
you do not own.
