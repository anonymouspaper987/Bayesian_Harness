#!/bin/bash
# The 157 masked real-API migrations, all 7 policies, for one model and budget.
#
# Usage: scripts/run_maskedmig.sh MODEL [BUDGET] [SEED]
#
# The seed only re-shuffles which quarter each migration is met in; the paper
# pools seeds 1, 2 and 3 and reports the s.d. across them.
#
# Needs the per-library pip environments built first:
#   python3 maskedmig/build_fresh_envs.py        # writes $MIG_ENVS/fresh_tars
# and a model server on $PORT (scripts/serve_vllm.sh).
set -eu
MODEL=${1:?model required}
BUDGET=${2:-24}
SEED=${3:-1}
PORT=${PORT:-8000}
LANES=${LANES:-"ours_hhmm,oracle bayesian_agent,ttl none,static,random"}

cd "$(dirname "$0")/.."
FRESH=${MASKEDMIG_DATA:-$PWD/data/maskedmig}
TAG=${TAG:-$(echo "$MODEL" | tr '/.' '__')}
LOGD=${RUN_LOG_DIR:-$PWD/logs}; mkdir -p "$LOGD"
mkdir -p "$FRESH/results"
PFX="$FRESH/results/${TAG}_s${SEED}_mb${BUDGET}"

PIDS=""
for L in $LANES; do
  python3 maskedmig/masked_loop.py --model "$MODEL" --port "$PORT" \
      --successors "$FRESH/successors_maskable_v2.json" \
      --checked "$FRESH/maskable_checked_v2.json" \
      --mask-style hash --conditions "$L" --workers 8 --seed "$SEED" \
      --maint-budget "$BUDGET" --t-scale 3 --out-prefix "$PFX" \
      > "$LOGD/maskedmig_${TAG}_s${SEED}_mb${BUDGET}_${L//,/-}.log" 2>&1 &
  PIDS="$PIDS $!"
done
wait $PIDS
echo "done: ${PFX}_*.jsonl"
