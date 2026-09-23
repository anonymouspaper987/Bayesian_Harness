#!/bin/bash
# Generate one acmesdk world from scratch: API specs, 36 monthly releases,
# changelogs, month-1 notes and the exam pool.
#
# Usage: scripts/build_world.sh SEED [SUFFIX] [EXTRA build_versions.py ARGS...]
#
#   scripts/build_world.sh 1                       # main world, calibrated drift
#   scripts/build_world.sh 1 _noburst --n-vertical 0 --n-horizontal 0   # uniform drift
#   scripts/build_world.sh 1 _dense   --n-vertical 12                   # more bursts
#
# The API surface is written by a language model, so a regenerated world is
# statistically equivalent to the shipped one but not byte-identical. The worlds
# used in the paper are in data/acmesdk/ already.
set -eu
SEED=${1:?seed required}; shift
SUFFIX=${1:-}; [ $# -gt 0 ] && shift || true
cd "$(dirname "$0")/.."

python3 acmesdk/build_versions.py --seed "$SEED" --suffix "$SUFFIX" "$@"
ACME_WORLD_SUFFIX="$SUFFIX" python3 acmesdk/make_tasks.py --seed "$SEED"
echo "world ready: data/acmesdk/seed${SEED}${SUFFIX}"
