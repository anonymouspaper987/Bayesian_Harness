#!/bin/bash
# Serve one open-weight model with vLLM on an OpenAI-compatible endpoint.
# The harness talks to http://localhost:$PORT/v1/chat/completions and nothing else,
# so any OpenAI-compatible server works; this script is only a convenience.
#
# Usage: scripts/serve_vllm.sh [MODEL] [PORT] [MAX_LEN] [GPU_UTIL]
#
# 32k context rather than 8k because a repair turn carries the broken snippet,
# the changelog slice and the sibling notes at once.
set -u
MODEL=${1:-Qwen/Qwen3.5-27B}
PORT=${2:-8000}
LEN=${3:-32768}
UTIL=${4:-0.90}
LOGD=${VLLM_LOG_DIR:-./logs}
mkdir -p "$LOGD"
LOG="$LOGD/vllm_$(echo "$MODEL" | tr /. __).log"

python3 -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" --port "$PORT" --max-model-len "$LEN" \
  --gpu-memory-utilization "$UTIL" --no-enable-log-requests > "$LOG" 2>&1 &

echo "starting, log at $LOG"
for _ in $(seq 1 180); do
  if curl -s "http://localhost:$PORT/v1/models" | grep -q "\"id\":\"$MODEL\""; then
    echo "### SERVER UP $MODEL port $PORT"; exit 0
  fi
  sleep 10
done
echo "### SERVER FAILED to come up in 30 min"; exit 1
