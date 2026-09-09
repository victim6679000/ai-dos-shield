#!/usr/bin/env bash
# Runs every profile direct and shielded, then builds all charts.
# This is the single command that regenerates every number in the PDF.
#   bash scripts/run_all.sh [duration_seconds]
set -euo pipefail
DUR=${1:-60}
INFER=${INFER:-http://localhost:8000}
SHIELD=${SHIELD:-http://localhost:8080}

echo "waiting for services..."
for url in "$INFER/health" "$SHIELD/health"; do
  for i in $(seq 1 60); do curl -sf "$url" >/dev/null && break || sleep 2; done
done

for p in normal spike flood expensive_low_rate sybil; do
  echo ""; echo "=== $p (direct) ==="
  python loadgen/run_experiment.py --profile "$p" --target "$INFER" --duration "$DUR"
  echo "=== $p (shielded) ==="
  python loadgen/run_experiment.py --profile "$p" --target "$SHIELD" --shield-url "$SHIELD" --duration "$DUR"
  D=$(ls -dt results/raw/*_${p}_direct | head -1)
  S=$(ls -dt results/raw/*_${p}_shield | head -1)
  python analysis/make_charts.py --direct "$D" --shield "$S" --outdir "results/charts/$p"
done
echo ""; echo "All charts in results/charts/"
