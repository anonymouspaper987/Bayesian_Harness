#!/bin/bash
# Reviewer zpd8, W2: Oracle with and without back-off after a failed repair, on AcmeSDK (budget 12, 36 months).
#   oracle            : paper default, the Oracle retries every broken note every month
#   oracle_backoff3   : --oracle-backoff 3, no retry for 3 months after a failed repair
#   ours              : Bayesian-Harness (ours_hhmm), same budget
#
# Usage: scripts/run_acmesdk_w2_oracle_backoff.sh [MODEL] [SEEDS]
#   e.g. scripts/run_acmesdk_w2_oracle_backoff.sh Qwen/Qwen3.5-4B "1 2 3"
# Needs a model server on $PORT (scripts/serve_vllm.sh) and built worlds (scripts/build_world.sh SEED).
set -eu
MODEL=${1:-Qwen/Qwen3.5-27B}
SEEDS=${2:-"1 2 3"}
BUDGET=12
BACKOFF=${BACKOFF:-3}
PORT=${PORT:-8000}

cd "$(dirname "$0")/.."
DATA=${ACMESDK_DATA:-$PWD/data/acmesdk}
TAG=${TAG:-$(echo "$MODEL" | tr '/.' '__')}
LOGD=${RUN_LOG_DIR:-$PWD/logs}; mkdir -p "$LOGD"

PIDS=""
for SEED in $SEEDS; do
  OUT="$DATA/seed${SEED}/results"; mkdir -p "$OUT"
  for V in oracle oracle_backoff$BACKOFF ours; do
    COND=oracle; EXTRA=""
    [ "$V" = ours ] && COND=ours_hhmm
    [ "$V" = oracle_backoff$BACKOFF ] && EXTRA="--oracle-backoff $BACKOFF"
    python3 acmesdk/loop.py --seed "$SEED" --model "$MODEL" --port "$PORT" \
        --conditions "$COND" --maint-budget "$BUDGET" --workers 8 --task-budget 3 $EXTRA \
        --out-prefix "$OUT/w2${V}_${TAG}_mb${BUDGET}" \
        > "$LOGD/acme_${TAG}_s${SEED}_mb${BUDGET}_${V}.log" 2>&1 &
    PIDS="$PIDS $!"
  done
done
wait $PIDS
echo "done: $DATA/seed*/results/w2*_${TAG}_mb${BUDGET}_*.jsonl"
