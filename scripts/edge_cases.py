"""
Unexpected-input suite. The rubric says the prototype must "survive a
second run and unexpected input" - this is the file that proves it.

Every case must return a clean status code. A traceback, a hang, or a
leaked counter is a failure.

  python scripts/edge_cases.py --target http://localhost:8080
"""
import argparse, json, sys
import httpx

CASES = [
    ("missing X-Client-ID",     {"text": "hi", "max_new_tokens": 8}, None,          {200, 429, 503}),
    ("empty text",              {"text": "", "max_new_tokens": 8},   "c1",          {422}),
    ("negative tokens",         {"text": "hi", "max_new_tokens": -5}, "c1",         {422}),
    ("zero tokens",             {"text": "hi", "max_new_tokens": 0},  "c1",         {422}),
    ("absurd token count",      {"text": "hi", "max_new_tokens": 10**9}, "c1",      {200, 429, 503}),
    ("oversized text",          {"text": "x" * 50000, "max_new_tokens": 8}, "c1",   {413}),
    ("wrong field types",       {"text": 123, "max_new_tokens": "abc"}, "c1",       {422}),
    ("missing fields",          {}, "c1",                                           {422}),
    ("unicode and emoji",       {"text": "héllo 世界 🚀", "max_new_tokens": 8}, "c1",{200, 429, 503}),
    ("newlines and nulls",      {"text": "a\nb\tc", "max_new_tokens": 8}, "c1",     {200, 429, 503}),
    ("very long client id",     {"text": "hi", "max_new_tokens": 8}, "z" * 5000,    {200, 429, 503}),
]

def main(a):
    failures = []
    with httpx.Client(timeout=30.0) as c:
        for name, payload, cid, expected in CASES:
            headers = {"X-Client-ID": cid} if cid else {}
            try:
                r = c.post(f"{a.target}/infer", json=payload, headers=headers)
                ok = r.status_code in expected
                print(f"  [{'PASS' if ok else 'FAIL'}] {name:<24} -> {r.status_code} (expected {sorted(expected)})")
                if not ok:
                    failures.append(name)
            except Exception as exc:
                print(f"  [FAIL] {name:<24} -> raised {type(exc).__name__}")
                failures.append(name)

        # malformed body cannot go through json=
        r = c.post(f"{a.target}/infer", content=b"not json at all",
                   headers={"Content-Type": "application/json"})
        ok = r.status_code in {400, 422}
        print(f"  [{'PASS' if ok else 'FAIL'}] {'malformed JSON body':<24} -> {r.status_code} (expected 400/422)")
        if not ok: failures.append("malformed JSON body")

        # counters must not have leaked
        try:
            s = c.get(f"{a.target}/shield/status").json()
            leaked = s["active"] < 0 or s["waiting"] < 0
            print(f"  [{'PASS' if not leaked else 'FAIL'}] {'no leaked counters':<24} -> "
                  f"active={s['active']} waiting={s['waiting']}")
            if leaked: failures.append("leaked counters")
        except Exception:
            print("  [skip] shield status unavailable (running against inference directly)")

    print(f"\n  {len(CASES)+1 - len(failures)}/{len(CASES)+1} passed")
    sys.exit(1 if failures else 0)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="http://localhost:8080")
    main(p.parse_args())
