# Docker interface test commands for Windows PowerShell

This runbook tests the repository from Docker on Windows. Run each block from a
PowerShell terminal without copying the `PS>` prompt itself.

The working browser interface today is FastAPI Swagger at the shield's `/docs`
endpoint. The custom live dashboard is a known Person 3 task: in the current
branch, `/dashboard` returns 404 because the HTML is not yet served by Docker.

## 1. Optional fresh clone

Use this while Person 1's PR is still open:

```powershell
$CloneDir = Join-Path ([Environment]::GetFolderPath('Desktop')) 'ai-dos-shield-fresh-test'
if (Test-Path -LiteralPath $CloneDir) { throw "Choose a new empty clone directory: $CloneDir" }
git clone --branch codex/person1-calibration --single-branch https://github.com/victim6679000/ai-dos-shield.git $CloneDir
Set-Location $CloneDir
Copy-Item .env.example .env
```

After PR #1 is merged, clone `main` normally instead:

```powershell
git clone https://github.com/victim6679000/ai-dos-shield.git $CloneDir
```

For the existing local checkout, start here instead:

```powershell
Set-Location 'C:\Users\fm916\OneDrive\Desktop\ai-project\ai-dos-shield'
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

## 2. Make Docker available in this terminal

Start Docker Desktop first. Then run:

```powershell
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $DockerCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'),
        (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin')
    )
    $DockerBin = $DockerCandidates | Where-Object { Test-Path (Join-Path $_ 'docker.exe') } | Select-Object -First 1
    if (-not $DockerBin) { throw 'Docker CLI was not found. Start or reinstall Docker Desktop.' }
    $env:Path = "$DockerBin;$env:Path"
}

docker version
docker compose version
```

Both commands must display client/server or Compose version information.

## 3. Choose the service addresses

Inference normally uses port 8000 and the shield uses port 8080.

```powershell
$InferenceUrl = 'http://127.0.0.1:8000'
$ShieldUrl = 'http://127.0.0.1:8080'
```

On the current Person 1 laptop, Burp Suite owns the IPv4 loopback address on
port 8080. Do not hard-code an old Wi-Fi address. Resolve the current active
address each time:

```powershell
$HostIPv4 = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | ForEach-Object { $_.IPv4Address.IPAddress } | Select-Object -First 1
if (-not $HostIPv4) { throw 'No active IPv4 address was found. Close Burp and use 127.0.0.1 instead.' }
$ShieldUrl = "http://${HostIPv4}:8080"
```

Show the selected addresses:

```powershell
"Inference: $InferenceUrl"
"Shield:    $ShieldUrl"
```

These URLs are for testing on the same machine. Do not create firewall rules or
publish the undefended inference service to the internet.

## 4. Build and start calibrated mock mode

`.env.example` defaults to mock mode. It uses Person 1's real measured timing
coefficients but avoids loading the model.

```powershell
Remove-Item Env:MOCK_MODEL -ErrorAction SilentlyContinue
docker compose config --quiet
docker compose up --build --detach --wait --wait-timeout 180
docker compose ps
```

Expected: both `inference` and `shield` show `healthy`.

If the installed Compose version does not support `--wait`, use:

```powershell
docker compose up --build --detach
docker compose ps
```

Wait until both services are healthy before continuing.

## 5. Test health and one request

```powershell
$InferenceHealth = Invoke-RestMethod "$InferenceUrl/health"
$ShieldHealth = Invoke-RestMethod "$ShieldUrl/health"
$InferenceHealth
$ShieldHealth
```

Expected:

- inference: `status=ok`, `model_ready=True`, `mock=True`;
- shield: `status=ok`, `mitigation_enabled=True`, `mode=NORMAL`.

Create one request:

```powershell
$Headers = @{ 'X-Client-ID' = 'interface-test-001' }
$Body = @{
    text = 'Explain why caching improves performance.'
    max_new_tokens = 16
} | ConvertTo-Json

$DirectRequest = @{
    Method = 'Post'
    Uri = "$InferenceUrl/infer"
    Headers = $Headers
    ContentType = 'application/json'
    Body = $Body
}

$ShieldRequest = @{
    Method = 'Post'
    Uri = "$ShieldUrl/infer"
    Headers = $Headers
    ContentType = 'application/json'
    Body = $Body
}

$DirectResponse = Invoke-RestMethod @DirectRequest
$ShieldResponse = Invoke-RestMethod @ShieldRequest
$DirectResponse
$ShieldResponse
```

Both successful responses must contain:

- `request_id`
- `output`
- `model_ms`
- `input_tokens`

The two `model_ms` values need not be identical because calibrated mock jitter
and runtime scheduling are intentional.

## 6. Open and test the current browser interface

```powershell
Start-Process "$ShieldUrl/docs"
```

In Swagger:

1. Expand `POST /infer`.
2. Select **Try it out**.
3. Enter `interface-browser-001` for `X-Client-ID`.
4. Use this JSON body:

```json
{
  "text": "Explain why caching improves performance.",
  "max_new_tokens": 16
}
```

5. Select **Execute** and confirm HTTP 200 plus the four response fields above.

You can also inspect health and live shield state in the browser:

```powershell
Start-Process "$ShieldUrl/health"
Start-Process "$ShieldUrl/shield/status"
```

## 7. Install the test client and run edge cases

Create a local Python 3.12 environment in the clone:

```powershell
py -3.12 -m venv .venv
$Python = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $Python -m pip install --disable-pip-version-check -r loadgen\requirements.txt
```

Run the suite against both paths:

```powershell
& $Python scripts\edge_cases.py --target $InferenceUrl
& $Python scripts\edge_cases.py --target $ShieldUrl
```

Expected: `12/12 passed` twice.

Check for leaked work after the tests:

```powershell
$InferenceMetrics = Invoke-RestMethod "$InferenceUrl/metrics"
$ShieldStatus = Invoke-RestMethod "$ShieldUrl/shield/status"
$InferenceMetrics
$ShieldStatus

if ($InferenceMetrics.active_requests -ne 0) { throw 'Inference active_requests leaked.' }
if ($ShieldStatus.active -ne 0) { throw 'Shield active counter leaked.' }
if ($ShieldStatus.waiting -ne 0) { throw 'Shield waiting counter leaked.' }
if ($ShieldStatus.inflight.clients_in_flight -ne 0) { throw 'Shield in-flight counter leaked.' }
```

## 8. Prove a second clean startup works

```powershell
docker compose down
docker compose up --detach --wait --wait-timeout 180
docker compose ps
& $Python scripts\edge_cases.py --target $ShieldUrl
```

Expected: both containers become healthy again and the suite again reports
`12/12 passed` without deleting state or changing configuration manually.

## 9. Test the real model

The first real startup downloads approximately 300 MB into `.hf_cache` and may
take several minutes.

```powershell
$env:MOCK_MODEL = 'false'
docker compose up --detach --force-recreate --wait --wait-timeout 300 inference shield
docker compose logs --tail 30 inference

$RealHealth = Invoke-RestMethod "$InferenceUrl/health"
$RealHealth
if ($RealHealth.mock -ne $false) { throw 'The real model did not start.' }

$RealHeaders = @{ 'X-Client-ID' = 'real-interface-test-001' }
$RealBody = @{
    text = 'Explain why caching improves performance.'
    max_new_tokens = 16
} | ConvertTo-Json

$RealDirectRequest = @{
    Method = 'Post'
    Uri = "$InferenceUrl/infer"
    Headers = $RealHeaders
    ContentType = 'application/json'
    Body = $RealBody
}

$RealShieldRequest = @{
    Method = 'Post'
    Uri = "$ShieldUrl/infer"
    Headers = $RealHeaders
    ContentType = 'application/json'
    Body = $RealBody
}

Invoke-RestMethod @RealDirectRequest
Invoke-RestMethod @RealShieldRequest
```

Expected: `mock=False` and both requests return a generated model response.

## 10. Restore calibrated mock mode

```powershell
Remove-Item Env:MOCK_MODEL -ErrorAction SilentlyContinue
docker compose up --detach --force-recreate --wait --wait-timeout 180 inference shield
Invoke-RestMethod "$InferenceUrl/health"
Invoke-RestMethod "$ShieldUrl/health"
```

Expected: inference reports `mock=True` again and both services remain healthy.

## 11. Optional short load demonstration

This is a smoke demonstration, not Person 2's canonical evidence run:

```powershell
& $Python loadgen\run_experiment.py --profile normal --target $InferenceUrl --duration 20
& $Python loadgen\run_experiment.py --profile normal --target $ShieldUrl --shield-url $ShieldUrl --duration 20
```

Person 2 must repair the evidence-integrity issues described in
`docs/MASTER_PROMPTS_PERSON_2_AND_3.md` before producing final project numbers.

## 12. Test the custom dashboard after Person 3 completes it

The following is an acceptance test for Person 3's future change. It is
expected to fail with HTTP 404 on the current Person 1 branch.

```powershell
$DashboardResponse = Invoke-WebRequest "$ShieldUrl/dashboard"
if ($DashboardResponse.StatusCode -ne 200) { throw 'Dashboard was not served.' }
if ($DashboardResponse.Headers.'Content-Type' -notlike 'text/html*') { throw 'Dashboard content type is not HTML.' }
Start-Process "$ShieldUrl/dashboard"
```

After Person 3's work, the page must update mode, queue, p50/p95, and counters
without a CORS error. It must also work when the shield host/port changes,
because the dashboard should use `location.origin`.

## 13. Logs and shutdown

Show recent logs:

```powershell
docker compose logs --tail 100 inference shield
```

Stop the project when testing is finished:

```powershell
docker compose down
```

Start it again later with:

```powershell
docker compose up --detach --wait --wait-timeout 180
```

## Technical demo rehearsal order

Use this order during the team rehearsal:

1. Show `docker compose ps` with both services healthy.
2. Open `$ShieldUrl/docs`.
3. Show `/health` for inference and shield.
4. Execute one normal `POST /infer` through the shield.
5. Show `/shield/status` and explain cost coefficients and counters.
6. Run a short authorized attack profile while the final dashboard is open.
7. Explain 429 client rejection, 503 global capacity rejection, and automatic
   NORMAL/PROTECTION switching using actual observed evidence.
8. End by showing Person 2's saved charts and the headline result.

Codex can verify these technical steps. The three teammates must still rehearse
their spoken parts, timing, handoffs, and answers on the final presentation
laptop.
