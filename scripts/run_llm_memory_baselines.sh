#!/bin/bash
# Reviewer TvTY, W1(c): LLM-driven prioritisation and two memory-system baselines on MaskedMig.
#
#   llm_rank  the model ranks which notes to check, once per quarter
#   mem0      Mem0-style update: the model marks a note NONE / UPDATE / DELETE after every exam
#   amem      A-Mem-style links: notes linked by content, a broken note flags its linked notes
#
# Main-table protocol: budget 24 per quarter, --t-scale 3, 157 migrations, seeds 1 2 3.
# Policies are in maskedmig/extra_baselines.py. LLM calls are not charged to the budget.
#
# Usage: scripts/run_llm_memory_baselines.sh [MODEL] [SEEDS]
#   e.g. scripts/run_llm_memory_baselines.sh Qwen/Qwen3.5-27B "1 2 3"
# Needs a model server on $PORT (scripts/serve_vllm.sh) and the per-library environments
# (python3 maskedmig/build_fresh_envs.py).
set -eu
MODEL=${1:-Qwen/Qwen3.5-27B}
SEEDS=${2:-"1 2 3"}
BUDGET=24
CONDS=${CONDS:-llm_rank,mem0,amem}
PORT=${PORT:-8000}

cd "$(dirname "$0")/.."
MIG=${MASKEDMIG_DATA:-$PWD/data/maskedmig}
TAG=${TAG:-$(echo "$MODEL" | tr '/.' '__')}
LOGD=${RUN_LOG_DIR:-$PWD/logs}; mkdir -p "$LOGD" "$MIG/results"

PIDS=""
for SEED in $SEEDS; do
  python3 maskedmig/masked_loop.py --model "$MODEL" --port "$PORT" \
      --successors "$MIG/successors_maskable_v2.json" --checked "$MIG/maskable_checked_v2.json" \
      --mask-style hash --conditions "$CONDS" --workers 8 --seed "$SEED" \
      --maint-budget "$BUDGET" --t-scale 3 --out-prefix "$MIG/results/tw1c_${TAG}_s${SEED}_mb${BUDGET}" \
      > "$LOGD/mig_${TAG}_s${SEED}.log" 2>&1 &
  PIDS="$PIDS $!"
done
wait $PIDS
echo "done: $MIG/results/tw1c_${TAG}_s*_mb${BUDGET}_*.jsonl"
