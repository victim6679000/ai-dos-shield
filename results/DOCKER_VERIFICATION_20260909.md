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
`127.0.0.1:8080`. Docker itself is healthy and is reachable on that laptop's
current Wi-Fi address at `http://10.10.197.1:8080`. A teammate running the
repository on a machine without that port conflict should use
`http://localhost:8080` as documented.

