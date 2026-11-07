#!/bin/bash
# Reviewer 5Xuf, W2: first-order approximation (used for the paper's numbers)
# vs. the exact period-by-period update of Eq. 8 (compose_per_period=1), on
# MaskedMig under the main-table protocol (budget 24, --t-scale 3).
# On AcmeSDK every update spans one period, so the two are identical there.
#
# Usage: scripts/run_maskedmig_w2_exact.sh [MODEL] [SEEDS]
#   e.g. scripts/run_maskedmig_w2_exact.sh Qwen/Qwen3.5-27B "1 2 3 4 5"
# Needs a model server on $PORT (scripts/serve_vllm.sh) and the per-library
# environments (python3 maskedmig/build_fresh_envs.py).
set -eu
MODEL=${1:-Qwen/Qwen3.5-27B}
SEEDS=${2:-"1 2 3 4 5"}
BUDGET=24
PORT=${PORT:-8000}

cd "$(dirname "$0")/.."
FRESH=${MASKEDMIG_DATA:-$PWD/data/maskedmig}
TAG=${TAG:-$(echo "$MODEL" | tr '/.' '__')}
LOGD=${RUN_LOG_DIR:-$PWD/logs}; mkdir -p "$LOGD" "$FRESH/results"

PIDS=""
for SEED in $SEEDS; do
  for V in approx exact; do
    EXTRA=""
    [ "$V" = exact ] && EXTRA="--cfg compose_per_period=1"
    PFX="$FRESH/results/w2${V}_${TAG}_s${SEED}_mb${BUDGET}"
    python3 maskedmig/masked_loop.py --model "$MODEL" --port "$PORT" \
        --successors "$FRESH/successors_maskable_v2.json" \
        --checked "$FRESH/maskable_checked_v2.json" \
        --mask-style hash --conditions ours_hhmm --workers 8 --seed "$SEED" \
        --maint-budget "$BUDGET" --t-scale 3 $EXTRA --out-prefix "$PFX" \
        > "$LOGD/maskedmig_w2${V}_${TAG}_s${SEED}_mb${BUDGET}.log" 2>&1 &
    PIDS="$PIDS $!"
  done
done
wait $PIDS
echo "done: $FRESH/results/w2{approx,exact}_${TAG}_s*_mb${BUDGET}_ours_hhmm.jsonl"
