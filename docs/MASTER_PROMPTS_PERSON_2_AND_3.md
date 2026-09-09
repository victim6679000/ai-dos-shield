# Master prompts for Persons 2 and 3

Each section below is a separate, self-contained prompt. Person 2 should copy
only the Person 2 section into a fresh coding-agent task. Person 3 should copy
only the Person 3 section into a different task. They must work on separate Git
branches because both will continue from Person 1's branch.

---

# START OF PERSON 2 MASTER PROMPT

You are Person 2, the load-generation, experimental-design, statistics, and
evidence owner for the existing `ai-dos-shield` repository. Build on the
existing implementation. Do not start over.

## Repository and branch

- Repository: `https://github.com/victim6679000/ai-dos-shield`
- This is a private repository. Confirm that the owner added your GitHub account
  as a collaborator, accept the invitation, and authenticate Git on your
  machine. No API key is required.
- Person 1 pull request: `https://github.com/victim6679000/ai-dos-shield/pull/1`
- If the repository is not already on your machine, download it first:

```powershell
git clone https://github.com/victim6679000/ai-dos-shield.git
Set-Location ai-dos-shield
```

- Until that PR is merged, create your branch from
  `origin/codex/person1-calibration`, for example:

```powershell
git fetch origin
git switch --create codex/person2-evidence origin/codex/person1-calibration
```

- If PR #1 has already been merged, use these exact commands instead:

```powershell
git fetch origin
git switch --create codex/person2-evidence origin/main
```

- Never rewrite Person 1's history or commit directly to `main`.

## Read before changing anything

Read and explain back:

1. `docs/00_SHARED_CONTRACT.md`
2. `docs/02_ROLE_B_Load_and_Evidence.md`
3. `docs/PERSON1_HANDOFF.md`
4. `results/CALIBRATION_HANDOFF.md`
5. `loadgen/run_experiment.py`
6. `analysis/make_charts.py`
7. Existing raw results, `results/charts/summary.json`, and the README claims

Explain the five traffic profiles, closed-loop legitimate traffic, open-loop
attack traffic, why legitimate p95 must always be paired with legitimate
success rate, and why ground-truth labels must stay private from the service.

## Ownership and boundaries

You own:

- `loadgen/`
- `analysis/make_charts.py`
- new Person 2 tests
- new experiment artifacts beneath `results/`
- `results/PERSON2_HANDOFF.md`

Do not edit:

- `inference/`
- `analysis/calibrate.py`
- `results/raw/calibration.json`
- `results/CALIBRATION_HANDOFF.md`
- `shield/`
- Dockerfiles or mitigation policy
- the final case PDF

You measure and report the shield; Person 3 decides and tunes its policy. If a
frozen interface blocks measurement, report the evidence before requesting a
cross-ownership change.

Run stress traffic only against localhost or another team-owned, explicitly
authorized endpoint. Never point the load generator at an external service.

## Frozen Person 1 starting state

Person 1 completed inference timing and calibration:

- `model_ms` times `model.generate(...)` only.
- Successful `/infer` responses contain `request_id`, `output`, `model_ms`,
  and additive `input_tokens`.
- Model input truncation is 512 tokens; output is capped at 256 tokens.
- The authoritative real-model fit is:

```text
model_ms = 510.063 + 1.258353*input_tokens + 26.005798*max_new_tokens
R² = 0.8912
modelled range = 731 ms to 6,147 ms (8.4x)
```

The calibration artifact and coefficients are measured evidence. Preserve
them exactly; do not alter them to improve an experimental result.

## Existing evidence is not final

Treat the existing results as exploratory and preserve them:

- Only one short flood direct/shield pair is present.
- Important model mode, Git, and complete configuration metadata are absent.
- The saved shield samples remained NORMAL throughout that run.
- The README headline and `results/charts/summary.json` disagree on p95 and
  success values.

Do not quote either set as final. Do not delete, overwrite, or cherry-pick old
or inconvenient runs.

## Required work

### 1. Harden the experiment harness before final runs

Make minimal, tested changes that address these evidence-integrity problems:

- Replace `legit-*`, `atk-*`, and `sybil-*` wire-visible IDs with opaque,
  neutral client IDs. Ground truth may exist only in memory and saved evidence,
  never in the URL, headers, request body, or client ID.
- Give every actor its own deterministic random-number stream. Do not use one
  shared RNG whose output changes with coroutine scheduling.
- Schedule open-loop traffic against absolute monotonic deadlines so slow
  requests do not silently reduce the intended arrival rate.
- Add explicit `direct`, `shield`, and `passthrough` run labels. Never infer the
  test type from port 8080.
- Reset all module state before every in-process run.
- Make health, selected real/mock mode, warm-up, and shield polling strict.
  Failed warm-ups or missing status data must fail clearly, not be swallowed.
- Warm inference directly and outside the measured interval.
- Record request start and completion timestamps additively while retaining
  useful existing columns.
- Record returned `request_id`, `input_tokens`, `model_ms`, HTTP status, latency,
  classification, and error details.
- Record start/end UTC, Git commit and dirty state, profile, seed, duration,
  timeout, corpus hash, deterministic plan hash, model health/config, shield
  pre/post status, run label, and effective profile specification.
- Calculate shield counters relative to their pre-run baseline instead of
  presenting unexplained process-lifetime totals.
- Never overwrite an existing run directory.
- Fail if a run has no request rows, or if a shielded run has no shield samples.
- Wait for inference to drain before the next pair. If timed-out requests leave
  queued model work, restart and warm inference before continuing.

Paired direct and shield runs must have identical open-loop attacker plans.
Legitimate users are closed-loop, so their total request counts may differ;
for each matching client, their common-prefix prompt and think-time sequences
must still match.

### 2. Correct the statistics

Use request start time to define the attack interval.

- Legitimate success rate = legitimate HTTP 200 responses divided by all
  legitimate attempts.
- Legitimate p50/p95 = successful legitimate end-to-end latency.
- Attack rejection rate = attack 429 or 503 responses divided by all attack
  attempts.
- Report timeouts, other 5xx responses, and every status code separately.
- For `normal`, use the full measured interval.
- For attack profiles, calculate both attack-window and whole-run metrics.
- Use one percentile implementation everywhere so console output, JSON, and
  charts cannot disagree.
- Handle zero-success and zero-attacker sets explicitly; do not convert missing
  data into a misleading 0 ms or 0% result.
- Reject paired analysis if profile, seed, duration, corpus/plan hash, model
  mode/config, or effective profile does not match.
- Never present a p95 improvement without the adjacent legitimate success rate.

### 3. Correct and regenerate charts

Required final figures:

1. Flood legitimate p95 over time, direct versus shield, with the true attack
   window shaded.
2. Flood legitimate p95 and legitimate success rate, before versus after.
3. Spike queue depth with real NORMAL → PROTECTION → NORMAL periods shaded.

The spike attack interval is 15–30 seconds; its shading must stop at 30 seconds.
All axes, units, profiles, model mode, and run identifiers must be visible.
Chart values must be derived from and agree exactly with saved summaries.

Generate a paste-ready headline sentence from the JSON evidence:

> Legitimate attack-window p95 fell from **X ms to Y ms** under sustained flood,
> with **Z%** of legitimate requests still served; **A%** of attack requests
> were rejected.

### 4. Give Person 3 a capacity baseline

Using the real model on the authorized demo laptop, increase load gradually
until direct legitimate p95 clearly degrades. Avoid freezing the laptop.

Give Person 3:

- normal baseline throughput and legitimate p50/p95;
- observed saturation region;
- success/timeout evidence;
- whether protection enters and recovers; and
- whether `/shield/status` reports Person 1's exact cost coefficients.

Do not tune the shield yourself.

### 5. Run the canonical experiment matrix

Wait until Person 3 confirms that:

- the calibrated cost coefficients are active;
- the shield input estimate is capped at 512 tokens;
- controller recovery is fixed;
- the dashboard/integration tests pass; and
- mitigation configuration is frozen.

Then run:

- `normal`, `spike`, `flood`, `expensive_low_rate`, and `sybil`;
- direct and shielded;
- 60 seconds per run with paired seed/profile/plan;
- twice, without deleting or replacing the first repetition; and
- a pass-through proxy control with mitigation disabled.

Use `google/flan-t5-small` with `mock=false`, two inference workers, and two
Torch threads for canonical evidence unless the team explicitly records a
mock-mode fallback. Record the mode in every run.

Before each shield run, start from clean shield state and verify NORMAL,
`active=0`, `waiting=0`, and no clients in flight. Use a fixed warm-up/cooldown
procedure and alternate pair order between repetitions to reduce thermal and
order bias.

Create an immutable manifest listing every final run and validity check. Use a
predeclared median/range aggregation rule rather than selecting the nicest run.

## Automated acceptance tests

Add `tests/test_person2.py` using the repository's existing standard-library
`unittest` approach. At minimum prove:

- exactly five required profiles exist;
- ground truth never appears on the wire or in a client ID;
- one seed gives the same plan hash and attacker event sequence;
- actor RNG streams are independent of completion order;
- Sybil client IDs are unique but opaque;
- attack-window boundaries are correct, including spike 15–30 seconds;
- known sample data produces the expected p50/p95;
- 429/503 are defence rejections while timeout/other 5xx remain failures;
- mismatched run pairs are rejected;
- a second in-process run begins with empty state;
- missing health, failed warm-up, missing shield samples, and empty results fail
  loudly; and
- console metrics and saved JSON agree.

## Definition of done

You are not done until:

- all new tests and all existing tests pass;
- every final directory has nonempty `requests.csv` and `config.json`;
- every shield run has nonempty roughly 2 Hz `shield.csv` data;
- final health proves real model, two workers, and two Torch threads;
- shield status contains Person 1's exact coefficients;
- legitimate traffic is present during every attack;
- the five profiles complete twice without manual deletion;
- spike evidence contains real entry into and recovery from protection;
- normal traffic remains healthy;
- improvements are not created by blocking legitimate users;
- pass-through data measures proxy-only overhead;
- all chart values match saved summaries; and
- `results/PERSON2_HANDOFF.md` gives Person 1 exact PDF numbers, chart paths,
  anomalies, and limitations.

Commit only intentional Person 2/evidence files, push your feature branch, and
open a PR. Report exact commands, results, run directories, failures, and
remaining Person 3 dependencies. Do not merge `main` yourself.

# END OF PERSON 2 MASTER PROMPT

---

# START OF PERSON 3 MASTER PROMPT

You are Person 3, owner of the mitigation gateway, Docker integration, live
dashboard, and resilience testing for the existing `ai-dos-shield` repository.
Build on the existing implementation. Do not start over.

## Repository and branch

- Repository: `https://github.com/victim6679000/ai-dos-shield`
- This is a private repository. Confirm that the owner added your GitHub account
  as a collaborator, accept the invitation, and authenticate Git on your
  machine. No API key is required.
- Person 1 pull request: `https://github.com/victim6679000/ai-dos-shield/pull/1`
- If the repository is not already on your machine, download it first:

```powershell
git clone https://github.com/victim6679000/ai-dos-shield.git
Set-Location ai-dos-shield
```

- Until that PR is merged, create your branch from
  `origin/codex/person1-calibration`, for example:

```powershell
git fetch origin
git switch --create codex/person3-shield origin/codex/person1-calibration
```

- If PR #1 has already been merged, use these exact commands instead:

```powershell
git fetch origin
git switch --create codex/person3-shield origin/main
```

- Never rewrite Person 1's history or commit directly to `main`.

## Read before changing anything

Read and explain back:

1. `docs/00_SHARED_CONTRACT.md`
2. `docs/03_ROLE_C_Shield_and_Integration.md`
3. `docs/PERSON1_HANDOFF.md`
4. `results/CALIBRATION_HANDOFF.md`
5. `.env.example`
6. `shield/app.py`
7. `shield/controller.py`
8. `shield/cost.py`
9. `shield/limiter.py`
10. `docker-compose.yml`
11. `dashboard/index.html`
12. `scripts/edge_cases.py`

Explain the four defence mechanisms, their order, what causes 429 versus 503,
and how NORMAL/PROTECTION hysteresis works before editing.

## Ownership and boundaries

You own:

- `shield/`
- `docker-compose.yml`
- Docker integration files
- `scripts/`
- `dashboard/`
- new Person 3 tests
- a new shield-tuning/integration handoff beneath `results/`

Do not modify:

- `inference/`
- `analysis/calibrate.py`
- `results/raw/calibration.json`
- `results/CALIBRATION_HANDOFF.md`
- `loadgen/` or `analysis/make_charts.py`
- Person 1's fitted coefficients

You may run Person 2 tools but may not rewrite their measurement policy. Report
a concrete frozen-interface blocker before requesting cross-ownership edits.

Stress testing is restricted to local or explicitly authorized team systems.
Do not expose the intentionally undefended inference endpoint publicly, open
firewall rules, or test external targets.

## Frozen contract and calibration

- Both services keep `POST /infer` with request fields `text` and
  `max_new_tokens`.
- Preserve successful fields `request_id`, `output`, and `model_ms`.
- Preserve Person 1's additive `input_tokens` field through the shield.
- Preserve status meanings: 429 is per-client budget/in-flight rejection; 503
  is safe global-capacity rejection; 413 is oversized text; 422 is malformed
  input; 502/504 are upstream failure/timeout.
- Add fields only; do not rename or repurpose frozen fields.
- Shield decisions must never use `ground_truth_class`, `legitimate`, or
  `attack` labels.

The authoritative measured model is:

```text
model_ms = 510.063 + 1.258353*input_tokens + 26.005798*max_new_tokens
COST_C0=510.063
COST_C_IN=1.258353
COST_C_OUT=26.005798
R² = 0.8912
modelled range = 731 ms to 6,147 ms (8.4x)
```

The fit contains visible CPU variance and is useful for cost ranking rather
than perfect latency prediction. Do not restore the old `60/0.8/18` defaults
or repeat the stale 26x claim.

## Confirmed starting gaps

- `shield/cost.py` does not cap its character-based estimate at the model's
  512-token truncation.
- Some runtime fallback cost/protection values disagree with `.env.example`.
- The controller keeps the last 300 latencies indefinitely; after overload it
  may require many new requests instead of recovering when old samples age out.
- Waiting-counter exception/cancellation cleanup can leak this request or
  decrement a different request.
- The edge suite does not strictly require every final counter to equal zero.
- The static dashboard defaults to `http://localhost:8080`, is not packaged or
  served by the shield container, and `GET /dashboard` currently returns 404.

Fix these in owned files; do not work around them by weakening tests.

## Required work

### 1. Integrate calibrated request pricing

- Keep all coefficients environment-driven.
- Add/use `MAX_INPUT_TOKENS=512` in `shield/cost.py`.
- Cap the cheap estimate conceptually as
  `min(MAX_INPUT_TOKENS, max(1, len(text)/CHARS_PER_TOKEN))`.
- Expose the input cap and character/token approximation in
  `/shield/status.cost_model`.
- Confirm requested output is capped exactly as inference caps it before the
  request is priced and forwarded.
- Align code fallbacks with `.env.example`, including protection multiplier.
- Keep the final `COST_MAX_MS` safety cap.
- Never change Person 1's coefficients to manufacture a better result.

Use Person 2's normal/capacity baseline to retune only shield controls:
bucket capacity/refill, active/waiting bounds, protection scales, and controller
thresholds. Record each deliberate configuration change and its evidence.

### 2. Make controller latency history time-based

- Add a setting such as `CTRL_LATENCY_WINDOW_S=30.0` to `.env.example`.
- Store `(time.monotonic(), latency_ms)` samples.
- Prune expired samples while recording, evaluating, calculating p50/p95, and
  creating a status snapshot.
- Keep a bounded maximum sample count as a secondary memory limit.
- Preserve two-state NORMAL/PROTECTION hysteresis and consecutive checks.
- After high samples expire and occupancy is low, exactly `CTRL_EXIT_CHECKS`
  healthy evaluations must return the system to NORMAL without requiring new
  inference requests.
- Recent high samples must continue blocking premature recovery.
- Expose the latency-window duration in status thresholds.

### 3. Serve the live dashboard same-origin

- Serve the existing canonical `dashboard/index.html` at `GET /dashboard` and
  preferably `/dashboard/` from the shield.
- Change its default API base to `location.origin`; an optional `?shield=`
  override may remain.
- Package the single canonical file in the shield image. Do not maintain a
  second copied HTML file.
- If the Docker build context becomes the repository root, add a root
  `.dockerignore` excluding `.git`, `.env`, `.venv`, `.hf_cache`, caches, and
  bulky result data.
- Do not solve this with wildcard CORS.
- Do not insert a query-provided URL or error through unsafe `innerHTML`; use
  text content.
- Update README launch instructions to the HTTP `/dashboard` endpoint.

### 4. Make counter cleanup exact

- Track whether each request is registered as waiting.
- Use `finally` so every waiting increment has exactly one matching decrement,
  including cancellation before semaphore acquisition.
- Preserve exact active and per-client in-flight cleanup for success, timeout,
  injected failure, and cancellation.
- Never condition cleanup only on a global count being positive because that
  can decrement another request's slot.
- Strengthen `scripts/edge_cases.py` to require `active == 0`, `waiting == 0`,
  and `inflight.clients_in_flight == 0`.
- Preserve the shared async HTTP client, bounded semaphore/queue, limiter lock,
  idle-bucket expiry, and explicit upstream timeouts.

### 5. Add deterministic tests

Use standard-library `unittest` and retain all Person 1 tests. Cover:

- input-cost clamping at 512 tokens;
- cost ordering and final safety cap;
- token-bucket concurrency and no overspend;
- per-client in-flight enforcement and cleanup;
- time-window pruning;
- entry and exit consecutive-check streaks;
- idle recovery with zero new requests after the latency window expires;
- waiting/active/in-flight cleanup on success, timeout, exception, and task
  cancellation; and
- dashboard route/static packaging where practical.

Do not treat 429 and 503 as interchangeable in mechanism-specific tests.

## Acceptance criteria

### Unit and static

- `python -m unittest discover -s tests -v` passes, including Person 1 tests.
- `python -m py_compile` succeeds for every changed Python file.
- Pricing stops increasing after the 512-token input estimate.
- A 256-output-token request costs more than a 16-token request.
- `/shield/status.cost_model` reports 510.063, 1.258353, 26.005798, and the
  512-token cap under `.env.example`.
- After the latency window expires, exactly the configured healthy exit checks
  return PROTECTION to NORMAL without new latency samples.
- All counters equal zero after success, error, timeout, and cancellation.

### Docker, API, browser, and resilience

- `docker compose config`, Docker build, and recreate succeed.
- Both containers become healthy.
- Direct and shielded valid requests preserve required and additive response
  fields.
- The edge suite passes against both endpoints twice in succession.
- Costly repeat work from one client produces 429 with `Retry-After`.
- Enough concurrent distinct clients fill bounded capacity and produce fast
  503 responses while active/waiting never exceed configured limits.
- Recent sustained pressure produces NORMAL → PROTECTION only after the entry
  streak, and low pressure produces PROTECTION → NORMAL only after expiry and
  the exit streak.
- Stop inference: shield returns clean 502/504 without hanging or leaking
  counters. Restart inference: shield succeeds again without being restarted.
- `GET /dashboard` returns HTTP 200 `text/html` from the built container.
- The dashboard displays live mode, queue, latency, and counters without CORS
  errors and works on a non-default host port through `location.origin`.
- Bring the stack down/up and repeat smoke and edge checks without stale state.

### Tuning integrity

- Normal shielded traffic retains at least 95% legitimate success, targeting
  100%, and the proxy does not materially inflate normal p95.
- Paired flood and expensive-low-rate runs lower legitimate attack-window p95
  while legitimate success remains high.
- Report attacker 429 and 503 separately.
- Do not accept a latency improvement created by rejecting legitimate users.
- Preserve identity-rotation and trusted `X-Client-ID` limitations explicitly.
- Do not overwrite Person 2's canonical charts or claim unsaved results.

## Delivery

Create a concise handoff under `results/` containing:

- files changed;
- exact final shield configuration;
- tests and outcomes;
- final dashboard URL;
- direct/shield smoke and failure-recovery evidence;
- tuning run directories and metrics;
- honest limitations; and
- the exact state Person 2 should use for canonical runs.

Commit only intentional Person 3/shared-integration files, push your feature
branch, and open a PR. Do not merge `main` yourself. Report remaining failures
instead of hiding or weakening them.

# END OF PERSON 3 MASTER PROMPT
