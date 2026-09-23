#!/bin/bash
# One acmesdk cell: 36 months x 7 maintenance policies, for one model, seed and budget.
#
# Usage: scripts/run_acmesdk.sh MODEL SEED BUDGET [WORLD_SUFFIX]
#   scripts/run_acmesdk.sh Qwen/Qwen3.5-27B 1 24
#   scripts/run_acmesdk.sh gemini-2.5-flash-lite 1 24
#   scripts/run_acmesdk.sh Qwen/Qwen3.5-27B 1 24 _noburst
#
# Open-weight models need a server (scripts/serve_vllm.sh) on $PORT.
# Model names starting with "gemini" go to Vertex AI instead; see README.
#
# Policies run in four lanes so one cell saturates a batching server. Results go to
# data/acmesdk/seed<SEED><SUFFIX>/results/<tag>_mb<BUDGET>_<policy>.jsonl
set -eu
MODEL=${1:?model required}
SEED=${2:-1}
BUDGET=${3:-24}
SUFFIX=${4:-}
PORT=${PORT:-8000}
LANES=${LANES:-"ours_hhmm,oracle bayesian_agent,ttl none,static random"}

cd "$(dirname "$0")/.."
DATA=${ACMESDK_DATA:-$PWD/data/acmesdk}
# Result files are named after TAG; set it to whatever the analysis scripts expect
# (they default to "qwen27b"), otherwise it is derived from the model name.
TAG=${TAG:-$(echo "$MODEL" | tr '/.' '__')}
OUT="$DATA/seed${SEED}${SUFFIX}/results"; mkdir -p "$OUT"
LOGD=${RUN_LOG_DIR:-$PWD/logs}; mkdir -p "$LOGD"

PIDS=""
for L in $LANES; do
  python3 acmesdk/loop.py --seed "$SEED" --model "$MODEL" --port "$PORT" \
      --conditions "$L" --maint-budget "$BUDGET" --workers 8 --task-budget 3 \
      ${SUFFIX:+--world-suffix "$SUFFIX"} \
      --out-prefix "$OUT/${TAG}_mb${BUDGET}" \
      > "$LOGD/acmesdk_${TAG}_s${SEED}${SUFFIX}_mb${BUDGET}_${L//,/-}.log" 2>&1 &
  PIDS="$PIDS $!"
done
wait $PIDS
echo "done: $OUT/${TAG}_mb${BUDGET}_*.jsonl"
