"""
Chart generator. Every figure comes from saved CSV - nothing hand-drawn.

  python analysis/make_charts.py --direct results/raw/<run>_direct --shield results/raw/<run>_shield

Produces:
  chart1_p95_over_time.png   legitimate latency over time, attack window marked
  chart2_before_after.png    legitimate p95 + success rate, direct vs shielded
  chart3_queue_and_mode.png  queue depth and automatic mode transitions
  summary.json               the exact numbers to quote in the PDF
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams.update({"figure.dpi": 140, "font.size": 9, "axes.grid": True,
                     "grid.alpha": 0.3, "axes.spines.top": False, "axes.spines.right": False})

OK, DIRECT_C, SHIELD_C = 200, "#c0392b", "#1a7f5a"


def load(run_dir: Path):
    df = pd.read_csv(run_dir / "requests.csv")
    cfg = json.loads((run_dir / "config.json").read_text())
    shield = None
    if (run_dir / "shield.csv").exists():
        shield = pd.read_csv(run_dir / "shield.csv")
    return df, cfg, shield


def legit(df):
    return df[df.ground_truth_class == "legitimate"]


def stats(df):
    L = legit(df)
    ok = L[L.status_code == OK]
    return {
        "legit_requests": int(len(L)),
        "legit_success": int(len(ok)),
        "legit_success_rate": round(100 * len(ok) / max(1, len(L)), 1),
        "legit_p50_ms": round(float(ok.latency_ms.quantile(0.50)), 1) if len(ok) else None,
        "legit_p95_ms": round(float(ok.latency_ms.quantile(0.95)), 1) if len(ok) else None,
        "attack_requests": int((df.ground_truth_class == "attack").sum()),
        "attack_rejected": int(((df.ground_truth_class == "attack") &
                                (df.status_code.isin([429, 503]))).sum()),
    }


def rolling_p95(df, window=5.0):
    L = legit(df)
    ok = L[L.status_code == OK].sort_values("t_rel")
    if ok.empty:
        return [], []
    xs, ys = [], []
    for t in range(0, int(ok.t_rel.max()) + 1):
        w = ok[(ok.t_rel > t - window) & (ok.t_rel <= t)]
        if len(w) >= 2:
            xs.append(t)
            ys.append(w.latency_ms.quantile(0.95))
    return xs, ys


def main(a):
    d_dir, s_dir = Path(a.direct), Path(a.shield)
    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    d_df, d_cfg, _ = load(d_dir)
    s_df, s_cfg, s_shield = load(s_dir)
    d_stats, s_stats = stats(d_df), stats(s_df)
    profile = d_cfg["profile"]

    attack_start = min([x["start"] for x in d_cfg["profile_spec"]["attackers"]], default=None)

    # ---- Chart 1: legitimate p95 over time -----------------------------
    fig, ax = plt.subplots(figsize=(7, 3.2))
    for df, lab, col in [(d_df, "No mitigation", DIRECT_C), (s_df, "Shielded", SHIELD_C)]:
        xs, ys = rolling_p95(df)
        ax.plot(xs, ys, label=lab, color=col, lw=1.8)
    if attack_start:
        ax.axvspan(attack_start, d_cfg["duration_s"], color="grey", alpha=0.12)
        ax.text(attack_start + 1, ax.get_ylim()[1] * 0.9, "attack window", fontsize=8, color="grey")
    ax.set_xlabel("time (s)"); ax.set_ylabel("legitimate p95 latency (ms)")
    ax.set_title(f"Legitimate-user tail latency during '{profile}'")
    ax.legend(frameon=False)
    fig.tight_layout(); fig.savefig(outdir / "chart1_p95_over_time.png"); plt.close(fig)

    # ---- Chart 2: before/after bars ------------------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7, 3.0))
    ax1.bar(["No mitigation", "Shielded"],
            [d_stats["legit_p95_ms"] or 0, s_stats["legit_p95_ms"] or 0],
            color=[DIRECT_C, SHIELD_C], width=0.55)
    ax1.set_ylabel("legitimate p95 (ms)"); ax1.set_title("Tail latency (lower is better)")
    for i, v in enumerate([d_stats["legit_p95_ms"] or 0, s_stats["legit_p95_ms"] or 0]):
        ax1.text(i, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=8)

    ax2.bar(["No mitigation", "Shielded"],
            [d_stats["legit_success_rate"], s_stats["legit_success_rate"]],
            color=[DIRECT_C, SHIELD_C], width=0.55)
    ax2.set_ylim(0, 105); ax2.set_ylabel("legitimate success rate (%)")
    ax2.set_title("Availability (higher is better)")
    for i, v in enumerate([d_stats["legit_success_rate"], s_stats["legit_success_rate"]]):
        ax2.text(i, v, f"{v:.1f}%", ha="center", va="bottom", fontsize=8)
    fig.suptitle(f"Profile: {profile}", fontsize=10)
    fig.tight_layout(); fig.savefig(outdir / "chart2_before_after.png"); plt.close(fig)

    # ---- Chart 3: queue depth + mode -----------------------------------
    if s_shield is not None and not s_shield.empty:
        fig, ax = plt.subplots(figsize=(7, 3.0))
        ax.plot(s_shield.t_rel, s_shield.active, label="active", color="#2c3e50", lw=1.5)
        ax.plot(s_shield.t_rel, s_shield.waiting, label="waiting (queued)", color="#e67e22", lw=1.5)
        prot = s_shield[s_shield["mode"] == "PROTECTION"]
        if not prot.empty:
            ax.fill_between(s_shield.t_rel, 0, ax.get_ylim()[1],
                            where=(s_shield["mode"] == "PROTECTION").values,
                            color=SHIELD_C, alpha=0.10, step="mid")
            ax.text(prot.t_rel.iloc[0], ax.get_ylim()[1] * 0.92,
                    " PROTECTION mode active (automatic)", fontsize=8, color=SHIELD_C)
        ax.set_xlabel("time (s)"); ax.set_ylabel("requests")
        ax.set_title("Bounded queue and automatic mode transition")
        ax.legend(frameon=False)
        fig.tight_layout(); fig.savefig(outdir / "chart3_queue_and_mode.png"); plt.close(fig)

    # ---- Summary --------------------------------------------------------
    p95_gain = (d_stats["legit_p95_ms"] / s_stats["legit_p95_ms"]
                if s_stats["legit_p95_ms"] else None)
    summary = {"profile": profile, "direct": d_stats, "shielded": s_stats,
               "p95_improvement_x": round(p95_gain, 1) if p95_gain else None,
               "direct_run": d_cfg["run_id"], "shield_run": s_cfg["run_id"]}
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\n  profile: {profile}")
    print(f"  legit p95      {d_stats['legit_p95_ms']:>9,.0f} ms  ->  {s_stats['legit_p95_ms']:>8,.0f} ms"
          f"   ({p95_gain:.1f}x better)")
    print(f"  legit success  {d_stats['legit_success_rate']:>9.1f} %   ->  {s_stats['legit_success_rate']:>8.1f} %")
    print(f"  attack rejected{s_stats['attack_rejected']:>9,} / {s_stats['attack_requests']:,} when shielded")
    print(f"\n  charts -> {outdir}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--direct", required=True)
    p.add_argument("--shield", required=True)
    p.add_argument("--outdir", default="results/charts")
    main(p.parse_args())
