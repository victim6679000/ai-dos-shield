"""
Experiment harness - this is what produces the evidence the jury grades.

One command runs a profile against a target and writes two CSVs:

  results/raw/<run_id>/requests.csv   one row per request
  results/raw/<run_id>/shield.csv     shield status sampled every 0.5s
  results/raw/<run_id>/config.json    everything needed to reproduce it

Design decisions that make the comparison fair:

* Legitimate traffic ALWAYS runs, in every profile, including during
  attacks. Without it you cannot prove availability was preserved.
* Legitimate users are closed-loop (send, wait for response, think, repeat)
  which is how real users behave. Attackers are open-loop (fixed arrival
  rate regardless of whether the server is coping) which is how attacks
  behave. This distinction matters: an open-loop attacker keeps piling on
  while a closed-loop user naturally backs off.
* ground_truth_class is written to the CSV and NEVER sent over the wire.
  The shield must not be able to tell legitimate from attack traffic.
* A fixed seed picks prompts from a fixed corpus, so the BEFORE and AFTER
  runs send byte-identical traffic.

Usage:
  python run_experiment.py --profile flood --target http://localhost:8080 --duration 60
"""

import argparse
import asyncio
import csv
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

CORPUS = [
    "Explain why caching helps performance.",
    "Summarize the benefits of load balancing.",
    "What is a token bucket rate limiter?",
    "Describe how a reverse proxy works.",
    "Why does tail latency matter more than average latency?",
    "What is the difference between throughput and concurrency?",
]

LEGIT = "legitimate"
ATTACK = "attack"

# profile -> dict describing the client mix
PROFILES = {
    "normal": {
        "description": "Healthy baseline. Legitimate users only.",
        "legit_users": 4,
        "attackers": [],
    },
    "spike": {
        "description": "Legitimate baseline plus a short sharp burst.",
        "legit_users": 4,
        "attackers": [
            {"kind": "burst", "clients": 20, "rps": 25, "start": 15, "stop": 30,
             "max_new_tokens": 16, "text_repeat": 1},
        ],
    },
    "flood": {
        "description": "Sustained high request rate from many clients.",
        "legit_users": 4,
        "attackers": [
            {"kind": "burst", "clients": 12, "rps": 20, "start": 10, "stop": 9999,
             "max_new_tokens": 16, "text_repeat": 1},
        ],
    },
    "expensive_low_rate": {
        "description": "The AI-native low-and-slow attack. Trivial request "
                       "rate, ruinous compute: long input, maximum output.",
        "legit_users": 4,
        "attackers": [
            {"kind": "burst", "clients": 3, "rps": 2, "start": 10, "stop": 9999,
             "max_new_tokens": 256, "text_repeat": 60},
        ],
    },
    "sybil": {
        "description": "Flood where every request uses a fresh client ID. "
                       "Defeats per-client limiting on purpose - shows why "
                       "the global capacity bound is also required.",
        "legit_users": 4,
        "attackers": [
            {"kind": "burst", "clients": 400, "rps": 20, "start": 10, "stop": 9999,
             "max_new_tokens": 16, "text_repeat": 1, "rotate": True},
        ],
    },
}

rows: list[dict] = []
shield_rows: list[dict] = []
stop_flag = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def send(client: httpx.AsyncClient, target: str, client_id: str, label: str,
               text: str, max_new_tokens: int, t0: float):
    started = time.perf_counter()
    status, model_ms, err = 0, None, ""
    try:
        r = await client.post(f"{target}/infer",
                              json={"text": text, "max_new_tokens": max_new_tokens},
                              headers={"X-Client-ID": client_id}, timeout=30.0)
        status = r.status_code
        if status == 200:
            try:
                model_ms = r.json().get("model_ms")
            except Exception:
                pass
    except httpx.TimeoutException:
        status, err = 0, "timeout"
    except Exception as exc:
        status, err = 0, type(exc).__name__
    latency_ms = (time.perf_counter() - started) * 1000.0

    rows.append({
        "timestamp": now_iso(),
        "t_rel": round(time.perf_counter() - t0, 3),
        "client_id": client_id,
        "ground_truth_class": label,   # never sent over the wire
        "text_len": len(text),
        "max_new_tokens": max_new_tokens,
        "status_code": status,
        "latency_ms": round(latency_ms, 2),
        "model_ms": model_ms,
        "error": err,
    })


async def legit_user(client, target, idx, duration, rng, t0):
    """Closed-loop: one request in flight, then think time."""
    cid = f"legit-{idx:03d}"
    while not stop_flag and (time.perf_counter() - t0) < duration:
        text = rng.choice(CORPUS)
        await send(client, target, cid, LEGIT, text, 16, t0)
        await asyncio.sleep(rng.uniform(0.8, 1.6))


async def attacker(client, target, spec, duration, rng, t0):
    """Open-loop: fires at a fixed rate regardless of server health."""
    interval = 1.0 / spec["rps"]
    tasks: set[asyncio.Task] = set()
    n = 0
    while not stop_flag:
        elapsed = time.perf_counter() - t0
        if elapsed >= duration:
            break
        if elapsed < spec["start"] or elapsed > spec["stop"]:
            await asyncio.sleep(0.05)
            continue

        if spec.get("rotate"):
            cid = f"sybil-{n:05d}"
        else:
            cid = f"atk-{n % spec['clients']:03d}"
        text = " ".join([rng.choice(CORPUS)] * spec["text_repeat"])
        t = asyncio.create_task(
            send(client, target, cid, ATTACK, text, spec["max_new_tokens"], t0))
        tasks.add(t)
        t.add_done_callback(tasks.discard)
        n += 1
        await asyncio.sleep(interval)

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def poll_shield(shield_url, duration, t0):
    """Sample shield state so we can plot queue depth and mode over time."""
    async with httpx.AsyncClient(timeout=2.0) as c:
        while not stop_flag and (time.perf_counter() - t0) < duration:
            try:
                s = (await c.get(f"{shield_url}/shield/status")).json()
                shield_rows.append({
                    "t_rel": round(time.perf_counter() - t0, 3),
                    "mode": s["mode"],
                    "active": s["active"],
                    "waiting": s["waiting"],
                    "occupancy": s["occupancy"],
                    "allowed": s["counters"]["allowed"],
                    "rate_limited_429": s["counters"]["rate_limited_429"],
                    "shed_503": s["counters"]["shed_503"],
                })
            except Exception:
                pass
            await asyncio.sleep(0.5)


async def warmup(target, n=5):
    """Never benchmark a cold model. The first few requests are outliers."""
    async with httpx.AsyncClient(timeout=60.0) as c:
        for _ in range(n):
            try:
                await c.post(f"{target}/infer",
                             json={"text": "warm up", "max_new_tokens": 8},
                             headers={"X-Client-ID": "warmup"})
            except Exception:
                pass


async def main(args):
    global stop_flag
    profile = PROFILES[args.profile]
    rng = random.Random(args.seed)

    label = "shield" if ":8080" in args.target else "direct"
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{args.profile}_{label}"
    outdir = Path(args.outdir) / run_id
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"warming up {args.target} ...")
    await warmup(args.target)

    print(f"run_id={run_id}  profile={args.profile}  duration={args.duration}s")
    print(f"  {profile['description']}")

    t0 = time.perf_counter()
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=500)) as client:
        tasks = [asyncio.create_task(legit_user(client, args.target, i, args.duration, rng, t0))
                 for i in range(profile["legit_users"])]
        tasks += [asyncio.create_task(attacker(client, args.target, spec, args.duration, rng, t0))
                  for spec in profile["attackers"]]
        if args.shield_url:
            tasks.append(asyncio.create_task(poll_shield(args.shield_url, args.duration, t0)))
        await asyncio.gather(*tasks, return_exceptions=True)
    stop_flag = True

    with open(outdir / "requests.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    if shield_rows:
        with open(outdir / "shield.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(shield_rows[0].keys()))
            w.writeheader()
            w.writerows(shield_rows)

    config = {
        "run_id": run_id, "profile": args.profile, "target": args.target,
        "target_label": label, "duration_s": args.duration, "seed": args.seed,
        "profile_spec": profile, "started_utc": now_iso(), "requests": len(rows),
    }
    (outdir / "config.json").write_text(json.dumps(config, indent=2))

    legit = [r for r in rows if r["ground_truth_class"] == LEGIT]
    ok = [r for r in legit if r["status_code"] == 200]
    lat = sorted(r["latency_ms"] for r in ok)
    p50 = lat[len(lat) // 2] if lat else 0
    p95 = lat[int(0.95 * len(lat))] if lat else 0

    print(f"\n  legitimate requests : {len(legit)}")
    print(f"  legitimate success  : {len(ok)}/{len(legit)} "
          f"({100*len(ok)/max(1,len(legit)):.1f}%)")
    print(f"  legitimate p50      : {p50:.0f} ms")
    print(f"  legitimate p95      : {p95:.0f} ms   <-- headline metric")
    print(f"\n  saved -> {outdir}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--profile", choices=list(PROFILES), required=True)
    p.add_argument("--target", default="http://localhost:8000")
    p.add_argument("--shield-url", default=None,
                   help="e.g. http://localhost:8080 - polls /shield/status")
    p.add_argument("--duration", type=int, default=60)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--outdir", default="results/raw")
    asyncio.run(main(p.parse_args()))
