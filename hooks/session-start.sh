#!/usr/bin/env bash
# Spawns the vaudeville inference daemon as a detached process.
# Called on SessionStart. Reads session_id from stdin JSON.
# Daemon binds to /tmp/vaudeville-{session_id}.sock.
# Fails open — if anything goes wrong, session continues normally.

set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

# Read session_id from stdin JSON
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('session_id','unknown'))" \
  2>/dev/null || echo "unknown")

SOCKET_PATH="/tmp/vaudeville-${SESSION_ID}.sock"
PID_FILE="/tmp/vaudeville-${SESSION_ID}.pid"
LOG_FILE="/tmp/vaudeville-${SESSION_ID}.log"

# Check if model cache exists
MODEL_CACHE="${HOME}/.cache/huggingface/hub/models--mlx-community--Phi-3-mini-4k-instruct-4bit"
if [ ! -d "${MODEL_CACHE}" ]; then
  echo "[vaudeville] Model not downloaded. Run: cd ${PLUGIN_ROOT} && uv run python -m vaudeville.setup" >&2
  exit 0
fi

# Check if daemon already running for this session
# Daemon uses fcntl PID lock (self-healing on crash) — this check is defense-in-depth
if [ -f "${PID_FILE}" ]; then
  PID=$(cat "${PID_FILE}" 2>/dev/null || echo "")
  if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
    echo "[vaudeville] Daemon already running (PID ${PID})" >&2
    exit 0
  fi
  # Stale PID — clean up
  rm -f "${PID_FILE}" "${SOCKET_PATH}"
fi

# Spawn daemon as detached process (nohup + disown mirrors detached:true + unref())
nohup uv run --project "${PLUGIN_ROOT}" python -m vaudeville.server \
  --socket "${SOCKET_PATH}" \
  --pid-file "${PID_FILE}" \
  >> "${LOG_FILE}" 2>&1 &
DAEMON_PID=$!
disown "${DAEMON_PID}"

echo "[vaudeville] Daemon spawned (PID ${DAEMON_PID}, socket ${SOCKET_PATH})" >&2
exit 0
