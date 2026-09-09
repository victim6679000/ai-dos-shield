"""
Cost model calibration.

Sweeps input length x max_new_tokens against the REAL inference server,
fits  model_ms ~= c0 + c_in*input_tokens + c_out*max_new_tokens  by least
squares, reports R^2, and prints the env lines to paste into .env.

This is the step that turns the limiter's "credits" from a guess into a
measured estimate of compute. Run it once on the demo laptop, on Day 2.

  python analysis/calibrate.py --target http://localhost:8000
"""
import argparse, json, time, itertools
from pathlib import Path
import httpx, numpy as np

def main(a):
    in_lens = [40, 400, 1200, 3000]
    out_toks = [8, 32, 96, 192]
    rows = []
    with httpx.Client(timeout=180.0) as c:
        print("warming up..."); [c.post(f"{a.target}/infer", json={"text":"warm","max_new_tokens":8}) for _ in range(3)]
        for L, T in itertools.product(in_lens, out_toks):
            text = "word " * (L // 5)
            samples = []
            for _ in range(a.repeats):
                r = c.post(f"{a.target}/infer", json={"text": text, "max_new_tokens": T},
                           headers={"X-Client-ID": "calibration"})
                if r.status_code == 200:
                    samples.append(r.json()["model_ms"])
                time.sleep(0.05)
            if samples:
                ms = float(np.median(samples))
                rows.append({"input_tokens": len(text)/4, "max_new_tokens": T, "model_ms": ms})
                print(f"  in={L:>5}ch out={T:>4}tok -> {ms:8.1f} ms")

    X = np.array([[1.0, r["input_tokens"], r["max_new_tokens"]] for r in rows])
    y = np.array([r["model_ms"] for r in rows])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ coef
    r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    c0, c_in, c_out = coef

    print(f"\n  model_ms = {c0:.1f} + {c_in:.3f}*input_tokens + {c_out:.2f}*max_new_tokens")
    print(f"  R^2 = {r2:.4f}   (report this number in the PDF)")
    print(f"\n  cheapest request modelled: {c0 + c_in*10 + c_out*8:.0f} ms")
    print(f"  most expensive modelled  : {c0 + c_in*750 + c_out*192:.0f} ms")
    print(f"  cost ratio               : {(c0 + c_in*750 + c_out*192)/(c0 + c_in*10 + c_out*8):.1f}x")
    print("\n--- paste into .env ---")
    print(f"COST_C0={c0:.1f}\nCOST_C_IN={c_in:.3f}\nCOST_C_OUT={c_out:.2f}")

    out = Path("results/raw/calibration.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"samples": rows, "c0": c0, "c_in": c_in, "c_out": c_out, "r2": r2}, indent=2))
    print(f"\nsaved -> {out}")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="http://localhost:8000")
    p.add_argument("--repeats", type=int, default=3)
    main(p.parse_args())
