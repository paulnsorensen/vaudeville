#!/usr/bin/env bash
# Spawns the vaudeville inference daemon as a detached process.
# Called on SessionStart. Reads session_id from stdin JSON.
# Daemon binds to /tmp/vaudeville-{session_id}.sock.
# Fails open — if anything goes wrong, session continues normally.

set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

# --- Architecture detection ---
ARCH=$(uname -m)
OS=$(uname -s)

if [[ "$OS" == "Darwin" && "$ARCH" == "arm64" ]]; then
  BACKEND="mlx"
  DEP_GROUP="mlx"
  MODEL_CACHE="${HOME}/.cache/huggingface/hub/models--mlx-community--Phi-3-mini-4k-instruct-4bit"
elif [[ "$ARCH" == "x86_64" || "$ARCH" == "aarch64" ]]; then
  BACKEND="gguf"
  DEP_GROUP="gguf"
  MODEL_CACHE="${HOME}/.cache/huggingface/hub/models--microsoft--Phi-3-mini-4k-instruct-gguf"
else
  echo "[vaudeville] Unsupported platform: ${OS}/${ARCH} — skipping daemon" >&2
  exit 0
fi

# Read session_id from stdin JSON
INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('session_id','unknown'))" \
  2>/dev/null || echo "unknown")
# Sanitize — session_id comes from stdin JSON, strip path-traversal chars
SESSION_ID=$(echo "$SESSION_ID" | tr -cd 'a-zA-Z0-9_-')
# Guard against empty string after sanitization (would cause path collisions)
if [ -z "${SESSION_ID}" ]; then
  SESSION_ID="unknown"
fi

SOCKET_PATH="/tmp/vaudeville-${SESSION_ID}.sock"
PID_FILE="/tmp/vaudeville-${SESSION_ID}.pid"
LOG_FILE="/tmp/vaudeville-${SESSION_ID}.log"

# Check if model cache exists
if [ ! -d "${MODEL_CACHE}" ]; then
  echo "[vaudeville] Model not downloaded (${BACKEND}). Run: cd ${PLUGIN_ROOT} && uv run --group ${DEP_GROUP} python -m vaudeville.setup" >&2
  exit 0
fi

# Check if daemon already running for this session
if [ -f "${PID_FILE}" ]; then
  PID=$(cat "${PID_FILE}" 2>/dev/null || echo "")
  if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
    echo "[vaudeville] Daemon already running (PID ${PID})" >&2
    exit 0
  fi
  # Stale PID — clean up
  rm -f "${PID_FILE}" "${SOCKET_PATH}"
fi

# Atomic spawn lock — mkdir is atomic on macOS and Linux.
# Prevents thundering herd when multiple sessions start concurrently.
SPAWN_LOCK="/tmp/vaudeville-${SESSION_ID}.spawn.lock"
cleanup_spawn_lock() { rm -rf "${SPAWN_LOCK}" 2>/dev/null; }
trap cleanup_spawn_lock EXIT
if ! mkdir "${SPAWN_LOCK}" 2>/dev/null; then
  echo "[vaudeville] Another session is spawning the daemon — skipping" >&2
  exit 0
fi

# Spawn daemon with platform-appropriate backend and deps
nohup uv run --project "${PLUGIN_ROOT}" --group "${DEP_GROUP}" \
  python -m vaudeville.server \
  --socket "${SOCKET_PATH}" \
  --pid-file "${PID_FILE}" \
  --backend "${BACKEND}" \
  >> "${LOG_FILE}" 2>&1 &
DAEMON_PID=$!
disown "${DAEMON_PID}"

echo "[vaudeville] Daemon spawned (${BACKEND}, PID ${DAEMON_PID}, socket ${SOCKET_PATH})" >&2
exit 0
