#!/bin/bash
# Reviewer TvTY, W1(b) / Q1: the release-aware baseline under both main-table protocols, for one model.
#
#   MaskedMig  (version-bump):     probe every note of a library whose installed version changed,
#                                  budget 24 per quarter, --t-scale 3, seeds 1 2 3
#   AcmeSDK    (changelog-aware):  probe every note named in this month's release notes,
#                                  budget 24 per month, 36 months, seeds 1 2 3
#
# Policy name: release_aware (class ReleaseAwareFilter in bayesian_harness/filters.py).
# Add ours_hhmm to CONDS to rerun Bayesian-Harness on the same stack, e.g. CONDS=release_aware,ours_hhmm.
#
# Usage: scripts/run_release_aware.sh [MODEL] [SEEDS]
#   e.g. scripts/run_release_aware.sh Qwen/Qwen3.5-27B "1 2 3"
# Needs a model server on $PORT (scripts/serve_vllm.sh), built worlds (scripts/build_world.sh SEED)
# and the per-library environments (python3 maskedmig/build_fresh_envs.py).
set -eu
MODEL=${1:-Qwen/Qwen3.5-27B}
SEEDS=${2:-"1 2 3"}
BUDGET=24
CONDS=${CONDS:-release_aware}
PORT=${PORT:-8000}

cd "$(dirname "$0")/.."
ACME=${ACMESDK_DATA:-$PWD/data/acmesdk}
MIG=${MASKEDMIG_DATA:-$PWD/data/maskedmig}
TAG=${TAG:-$(echo "$MODEL" | tr '/.' '__')}
LOGD=${RUN_LOG_DIR:-$PWD/logs}; mkdir -p "$LOGD" "$MIG/results"

PIDS=""
for SEED in $SEEDS; do
  OUT="$ACME/seed${SEED}/results"; mkdir -p "$OUT"
  python3 acmesdk/loop.py --seed "$SEED" --model "$MODEL" --port "$PORT" \
      --conditions "$CONDS" --maint-budget "$BUDGET" --workers 8 --task-budget 3 \
      --out-prefix "$OUT/tq1_${TAG}_mb${BUDGET}" \
      > "$LOGD/acme_${TAG}_s${SEED}_mb${BUDGET}.log" 2>&1 &
  PIDS="$PIDS $!"
  python3 maskedmig/masked_loop.py --model "$MODEL" --port "$PORT" \
      --successors "$MIG/successors_maskable_v2.json" --checked "$MIG/maskable_checked_v2.json" \
      --mask-style hash --conditions "$CONDS" --workers 8 --seed "$SEED" \
      --maint-budget "$BUDGET" --t-scale 3 --out-prefix "$MIG/results/tq1_${TAG}_s${SEED}_mb${BUDGET}" \
      > "$LOGD/mig_${TAG}_s${SEED}.log" 2>&1 &
  PIDS="$PIDS $!"
done
wait $PIDS
echo "done: $ACME/seed*/results/tq1_${TAG}_mb${BUDGET}_*.jsonl  $MIG/results/tq1_${TAG}_s*_mb${BUDGET}_*.jsonl"
