#!/usr/bin/env bash
# Spawns the vaudeville inference daemon as a global singleton.
# Called on SessionStart. Reads stdin to avoid SIGPIPE.
# Fails open — if anything goes wrong, session continues normally.

set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

# Always read stdin (Claude Code sends JSON; not reading causes SIGPIPE)
INPUT=$(cat)

# uv is required for dependency management
if ! command -v uv &>/dev/null; then
  echo "[vaudeville] uv not found. Run /vaudeville:setup to install prerequisites." >&2
  exit 0
fi

# Per-UID runtime directory (0700) prevents other users from intercepting socket
RUNTIME_DIR="/tmp/vaudeville-$(id -u)"
mkdir -m 0700 "${RUNTIME_DIR}" 2>/dev/null || true

SOCKET_PATH="${RUNTIME_DIR}/vaudeville.sock"
PID_FILE="${RUNTIME_DIR}/vaudeville.pid"
LOG_FILE="${RUNTIME_DIR}/vaudeville.log"
VERSION_FILE="${RUNTIME_DIR}/vaudeville.version"

# Write socket path to session env so subsequent hooks skip re-derivation
export_socket_path() {
  if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    printf 'export VAUDEVILLE_SOCKET=%q\n' "${SOCKET_PATH}" >> "${CLAUDE_ENV_FILE}"
  fi
}

# Check if model cache exists
MODEL_CACHE="${HOME}/.cache/huggingface/hub/models--mlx-community--Phi-3-mini-4k-instruct-4bit"
if [ ! -d "${MODEL_CACHE}" ]; then
  echo "[vaudeville] Model not downloaded. Run /vaudeville:setup to download it." >&2
  exit 0
fi

# Compute current version stamp
CURRENT_VERSION=$(git -C "${PLUGIN_ROOT}" rev-parse HEAD 2>/dev/null || echo "unknown")

# Check if daemon is already running
if [ -f "${PID_FILE}" ]; then
  PID=$(cat "${PID_FILE}" 2>/dev/null || echo "")
  if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
    # Daemon alive — check version
    RUNNING_VERSION=$(cat "${VERSION_FILE}" 2>/dev/null || echo "")
    if [ -z "${RUNNING_VERSION}" ] || [ "${RUNNING_VERSION}" = "${CURRENT_VERSION}" ]; then
      echo "[vaudeville] Daemon up to date (PID ${PID})" >&2
      export_socket_path
      exit 0
    fi
    # Version mismatch — restart daemon
    echo "[vaudeville] Version mismatch — restarting daemon (PID ${PID})" >&2
    kill "${PID}" 2>/dev/null || true
    # Wait up to 2s for socket to disappear (20 x 0.1s)
    for i in $(seq 1 20); do
      [ ! -S "${SOCKET_PATH}" ] && break
      sleep 0.1
    done
    # Force kill if still alive
    if kill -0 "${PID}" 2>/dev/null; then
      kill -9 "${PID}" 2>/dev/null || true
    fi
    rm -f "${SOCKET_PATH}" "${PID_FILE}" "${VERSION_FILE}" || true
  else
    # Stale PID — clean up
    echo "[vaudeville] Stale PID ${PID} — cleaning up" >&2
    rm -f "${SOCKET_PATH}" "${PID_FILE}" "${VERSION_FILE}" || true
  fi
fi

# Spawn daemon as detached process
nohup uv run --project "${PLUGIN_ROOT}" python -m vaudeville.server \
  --socket "${SOCKET_PATH}" \
  --pid-file "${PID_FILE}" \
  >> "${LOG_FILE}" 2>&1 &
DAEMON_PID=$!
disown "${DAEMON_PID}"

echo "[vaudeville] Daemon spawned (PID ${DAEMON_PID}, socket ${SOCKET_PATH})" >&2
export_socket_path
exit 0
