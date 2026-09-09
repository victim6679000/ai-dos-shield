# Person 1 completion and team handoff

Status date: 9 September 2026

This document records what Person 1 completed and what Persons 2 and 3 must
finish. It is a team handoff, not a replacement for the role guides.

## Git starting point

- Repository: `https://github.com/victim6679000/ai-dos-shield`
- The repository is private. The owner must add each teammate's GitHub account
  as a collaborator, and each teammate must accept the invitation before
  cloning. No API key is required.
- Person 1 branch: `codex/person1-calibration`
- Pull request: `https://github.com/victim6679000/ai-dos-shield/pull/1`
- The pull request is open and mergeable. Until it is merged, Persons 2 and 3
  must branch from `origin/codex/person1-calibration`, not the older `main`.
- Build on the existing files. Do not start the repository again from scratch.

## What Person 1 completed

### Inference timing

- Kept the existing FastAPI inference service and Dockerfile.
- Corrected `model_ms` so it measures only `model.generate(...)`.
- Tokenization, decoding, queueing, and network time are deliberately excluded.
- Added the effective `input_tokens` value to successful responses without
  changing or removing the required `request_id`, `output`, and `model_ms`
  fields.
- Exposed useful model/configuration metadata through `/health`.
- Kept input truncation at 512 model tokens and output at the configured cap.
- Added deterministic tests proving that tokenization and decoding are outside
  the model timer.

### Real-model calibration

`analysis/calibrate.py` now:

- refuses to calibrate a mock endpoint;
- checks that the real model is ready;
- runs the fixed 4 input sizes × 4 output sizes × 3 repeats protocol;
- retains all 48 raw observations;
- uses the median of the three repeats in each of the 16 cells;
- fits the cost equation by least squares;
- reports R², cheapest/most-expensive modelled work, and their ratio;
- writes the JSON evidence atomically; and
- prints paste-ready `COST_*` and `MOCK_*` settings.

The measured reference-laptop result is:

```text
model_ms = 510.063 + 1.258353*input_tokens + 26.005798*max_new_tokens
R² = 0.8912
modelled range = 731 ms to 6,147 ms
modelled cost ratio = 8.4x
```

The effective input-token grid is `10, 82, 242, 512`. The final value proves
that the 3,000-character request is measured after the model's 512-token
truncation rather than being priced as an impossible 750-token input.

These values are already present in `.env.example`:

```env
COST_C0=510.063
COST_C_IN=1.258353
COST_C_OUT=26.005798

MOCK_BASE_MS=510.063
MOCK_MS_PER_IN_TOKEN=1.258353
MOCK_MS_PER_OUT_TOKEN=26.005798
```

### Evidence and verification

- `results/raw/calibration.json`: 48 raw real-model measurements and 16 cell
  medians.
- `results/CALIBRATION_HANDOFF.md`: calibrated values and Person 3 integration
  note.
- `results/DOCKER_VERIFICATION_20260909.md`: Docker, runtime, and edge-test
  evidence.
- `tests/test_person1.py`: four deterministic Person 1 tests.
- Python 3.12 compilation and all four Person 1 tests pass.
- Direct inference edge suite: 12/12 passed.
- Shielded inference edge suite: 12/12 passed.
- Inference, shield, queue, and in-flight counters returned to zero.
- Both `MOCK_MODEL=true` and `MOCK_MODEL=false` were tested successfully.

## Independent clean-clone rehearsal

The GitHub branch was cloned into a new temporary directory and tested without
using the working checkout.

- Both Docker images built from scratch.
- Both containers became healthy in mock mode.
- Direct and shielded mock requests returned the required fields.
- Direct and shielded edge suites both passed 12/12.
- The stack was brought down and up a second time; the shield suite passed
  12/12 again without manual state cleanup.
- A fresh cache downloaded and loaded `google/flan-t5-small` in real mode.
- Direct and shielded real-model requests both returned HTTP 200.
- Swagger/OpenAPI at the shield's `/docs` endpoint returned HTTP 200.
- The original workspace stack was restored in calibrated mock mode afterward.

One expected integration gap was confirmed: `GET /dashboard` currently returns
404. The static `dashboard/index.html` is neither packaged into nor served by
the shield container, and its cross-origin status request is not enabled. This
is Person 3 work; it is not a Person 1 inference/calibration failure.

## Frozen Person 1 interfaces

Persons 2 and 3 should treat these as stable unless they report a concrete
blocker first:

- `POST /infer` request: `text` and `max_new_tokens`.
- Successful response: `request_id`, `output`, `model_ms`, plus additive
  `input_tokens`.
- `GET /health` and `GET /metrics` remain available on inference.
- `model_ms` means generation time only.
- `MAX_INPUT_TOKENS=512` and `MAX_NEW_TOKENS_CAP=256` are authoritative.
- The calibration coefficients and raw artifact must not be changed to make a
  load-test or shield result look better.
- No API key is needed. Testing must remain on local or explicitly authorized
  team machines; do not expose the intentionally undefended inference port to
  the public internet.

## What Person 2 must do

Person 2 owns `loadgen/`, `analysis/make_charts.py`, Person 2 tests, and new
experiment artifacts under `results/`.

1. Audit and minimally harden the experiment harness before final measurement.
2. Remove label-revealing client IDs from requests; ground truth may exist only
   in the evidence data.
3. Make direct/shield traffic plans deterministic and paired.
4. Record complete run metadata, model mode, Git revision, model/shield status,
   request start/completion times, response status, `request_id`,
   `input_tokens`, and `model_ms`.
5. Use attack-window p50/p95 and always report legitimate success beside
   latency improvement.
6. Give Person 3 a real-model capacity baseline before final shield tuning.
7. After Person 3 freezes the shield configuration, run all five profiles:
   `normal`, `spike`, `flood`, `expensive_low_rate`, and `sybil`.
8. Run direct and shielded pairs for 60 seconds with matching seed/config, twice
   without deleting the first runs. Also record a pass-through proxy control.
9. Regenerate all charts and summaries only from the saved final evidence.
10. Create `results/PERSON2_HANDOFF.md` containing exact PDF numbers, chart
    paths, anomalies, and honest limitations.

Existing results are exploratory only. The repository currently contains one
short flood pair, not the complete final matrix. The README headline and the
existing `summary.json` also disagree, so neither may be quoted as the final
answer until Person 2 regenerates consistent evidence.

## What Person 3 must do

Person 3 owns `shield/`, `docker-compose.yml`, `scripts/`, `dashboard/`, and
integration.

1. Cap the shield's character-based input estimate at
   `MAX_INPUT_TOKENS=512`, expose the cap in `/shield/status`, and keep the
   calibrated coefficients environment-driven.
2. Retune bucket capacity, refill, queue, and controller settings using Person
   2's baseline. Do not alter Person 1's fitted coefficients.
3. Replace the controller's count-only latency history with a bounded,
   time-based window so PROTECTION can recover after old latency samples expire.
4. Serve the existing dashboard from the shield at `/dashboard` using the same
   origin, package it in Docker without duplicating the HTML, and avoid wildcard
   CORS.
5. Fix waiting/active/in-flight cleanup so cancellation and failures cannot
   leak or decrement another request's counters.
6. Strengthen the edge suite so it requires all final counters to equal zero.
7. Add deterministic shield/controller/concurrency tests.
8. Verify distinct mechanisms: per-client 429, bounded-capacity 503, automatic
   NORMAL → PROTECTION → NORMAL recovery, clean 502/504 on inference failure,
   and recovery after inference restarts.
9. Run Docker and browser integration twice with no manual cleanup.
10. Freeze and document the final mitigation configuration before Person 2's
    canonical runs.

## Recommended team order

1. Review and merge PR #1, or branch directly from it while review is pending.
2. Person 2 hardens the measurement harness while Person 3 fixes deterministic
   shield/dashboard issues.
3. Person 2 measures the real-model baseline and gives it to Person 3.
4. Person 3 tunes, verifies, and freezes the shield configuration.
5. Person 2 executes the canonical experiment matrix and generates evidence.
6. Person 1 writes the five-page case PDF using the final Person 2 numbers and
   Person 3 mechanism/limitation handoff.
7. The whole team performs the spoken demo rehearsal on the final laptop.

## Person 1's remaining non-code task

Person 1's inference and calibration implementation is complete. Person 1 must
write `docs/CASE_PDF.md` only after the final evidence arrives. The document is
for the whole project and must contain exactly the required five sections,
remain at most five pages after export, and remain at most 20 MiB.
