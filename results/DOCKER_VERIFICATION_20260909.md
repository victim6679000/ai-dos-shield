# Docker verification — 9 September 2026 (GST)

## Environment

- Docker Desktop: 4.90.0
- Docker Engine: 29.7.2, Linux/amd64
- Docker Compose: 5.5.1
- Backend: WSL 2 (`desktop-linux`)

## Build result

Both Compose images built successfully from the repository:

- `ai-dos-shield-inference`
- `ai-dos-shield-shield`

The inference build installed `torch-2.5.1+cpu` from the PyTorch CPU index.
Runtime verification inside the container returned:

```text
Python 3.11.16
2.5.1+cpu False
```

The final `False` is `torch.cuda.is_available()`, confirming that the image is
CPU-only.

## Runtime result

The Compose stack started successfully in `MOCK_MODEL=true` mode. Both
containers became healthy on the first start and again after a complete
`docker compose down` / `docker compose up --detach` cycle.

```json
// inference :8000/health
{"status":"ok","model_ready":true,"mock":true}

// shield :8080/health
{"status":"ok","mitigation_enabled":true,"mode":"NORMAL"}
```

A shielded `POST /infer` returned HTTP 200 with the frozen response shape:

```json
{"request_id":"3ebc2bf3f86c","output":"[mock output for 16 tokens]","model_ms":321.19}
```

The unexpected-input suite passed all 12 cases against the shield, including
413/422 handling and counter cleanup:

```text
12/12 passed
no leaked counters -> active=0 waiting=0
```

## Reproduce

From the repository root:

```powershell
Copy-Item .env.example .env
docker compose config
docker compose up --build --detach
docker compose ps
python scripts/edge_cases.py --target http://localhost:8080
```

On the verification laptop, Burp Suite already owns the IPv4 loopback address
`127.0.0.1:8080`. Docker itself is healthy and was reachable at
`http://10.10.197.1:8080` during the initial test. Because a Wi-Fi address can
change, use `http://[::1]:8080` on this laptop to bypass Burp without depending
on the current network. A teammate running the repository on a machine without
that port conflict should use `http://localhost:8080` as documented.

## Person 1 timing and calibration verification

The inference image was rebuilt with `MOCK_MODEL=false`. The real
`google/flan-t5-small` model loaded successfully, `/health` reported
`model_ready=true`, and requests worked both directly and through the shield.
The response now also exposes the effective `input_tokens` as additive
diagnostic metadata.

The `model_ms` timer surrounds only `model.generate(...)`: tokenization,
decoding, queueing, and network time are excluded. A deterministic unit test
simulated 100 ms of tokenization, 250 ms of generation, and 150 ms of decoding;
the reported `model_ms` was 250 ms. A live real-model request also had a larger
end-to-end time than its reported generation time, as expected.

The real-model calibration completed the fixed 4 × 4 grid with three repeats
per cell:

```text
48 raw observations
16 per-cell medians
effective input-token lengths: 10, 82, 242, 512
model_ms = 510.063 + 1.258353*input_tokens + 26.005798*max_new_tokens
R² = 0.8912
modelled range = 731 ms to 6,147 ms (8.4x)
```

The 3,000-character input is correctly recorded as 512 effective tokens after
model truncation. `analysis/calibrate.py` rejected a mock-mode endpoint before
collecting samples, and the existing calibration artifact remained unchanged
during that negative test. The committed evidence is
`results/raw/calibration.json`; `results/CALIBRATION_HANDOFF.md` contains the
values and the remaining Person 3 integration note.

After applying the calibrated values, both edge-case suites passed again:

```text
direct inference: 12/12 passed
shielded inference: 12/12 passed
inference active_requests: 0
shield active/waiting/clients_in_flight: 0/0/0
```

Repeated CPU timings contain visible variance, so the R² is reported as
measured rather than presented as a perfect predictor.
