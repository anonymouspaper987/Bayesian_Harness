#!/bin/bash
# Reviewer zpd8, Q1: what to do after a failed repair, on AcmeSDK (budget 12, 36 months).
#   reset          : paper default, staleness is reset after any repair attempt
#   keep_and_retry : --keep-stale-on-failed-repair 1, the note stays marked broken and is retried next period
#
# Usage: scripts/run_acmesdk_q1_failed_repair.sh [MODEL] [SEEDS]
#   e.g. scripts/run_acmesdk_q1_failed_repair.sh Qwen/Qwen3.5-4B "1 2 3"
# Needs a model server on $PORT (scripts/serve_vllm.sh) and built worlds (scripts/build_world.sh SEED).
set -eu
MODEL=${1:-Qwen/Qwen3.5-27B}
SEEDS=${2:-"1 2 3"}
BUDGET=12
PORT=${PORT:-8000}

cd "$(dirname "$0")/.."
DATA=${ACMESDK_DATA:-$PWD/data/acmesdk}
TAG=${TAG:-$(echo "$MODEL" | tr '/.' '__')}
LOGD=${RUN_LOG_DIR:-$PWD/logs}; mkdir -p "$LOGD"

PIDS=""
for SEED in $SEEDS; do
  OUT="$DATA/seed${SEED}/results"; mkdir -p "$OUT"
  for V in reset keep_and_retry; do
    EXTRA=""
    [ "$V" = keep_and_retry ] && EXTRA="--keep-stale-on-failed-repair 1"
    python3 acmesdk/loop.py --seed "$SEED" --model "$MODEL" --port "$PORT" \
        --conditions ours_hhmm --maint-budget "$BUDGET" --workers 8 --task-budget 3 $EXTRA \
        --out-prefix "$OUT/q1${V}_${TAG}_mb${BUDGET}" \
        > "$LOGD/acme_${TAG}_s${SEED}_mb${BUDGET}_${V}.log" 2>&1 &
    PIDS="$PIDS $!"
  done
done
wait $PIDS
echo "done: $DATA/seed*/results/q1{reset,keep_and_retry}_${TAG}_mb${BUDGET}_ours_hhmm.jsonl"
