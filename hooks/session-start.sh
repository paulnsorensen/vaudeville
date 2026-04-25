#!/usr/bin/env bash
# Spawns the vaudeville inference daemon as a global singleton.
# Called on SessionStart. Reads stdin to avoid SIGPIPE.
# Fails open — if anything goes wrong, session continues normally.

set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

# --- Architecture detection ---
ARCH=$(uname -m)
OS=$(uname -s)

if [[ "$OS" == "Darwin" && "$ARCH" == "arm64" ]]; then
  BACKEND="mlx"
  DEP_GROUP="mlx"
  MODEL_CACHE="${HOME}/.cache/huggingface/hub/models--mlx-community--Phi-4-mini-instruct-4bit"
elif [[ "$ARCH" == "x86_64" || "$ARCH" == "aarch64" ]]; then
  BACKEND="gguf"
  DEP_GROUP="gguf"
  MODEL_CACHE="${HOME}/.cache/huggingface/hub/models--microsoft--Phi-4-mini-instruct-gguf"
else
  echo "[vaudeville] Unsupported platform: ${OS}/${ARCH} — skipping daemon" >&2
  exit 0
fi

# Always read stdin (Claude Code sends JSON; not reading causes SIGPIPE)
cat > /dev/null

# uv is required for dependency management
if ! command -v uv &>/dev/null; then
  echo "[vaudeville] uv not found. Run /vaudeville:setup to install prerequisites." >&2
  exit 0
fi

# Per-UID runtime directory (0700) prevents other users from intercepting socket
# VAUDEVILLE_RUNTIME_DIR overrides the default (used by tests)
RUNTIME_DIR="${VAUDEVILLE_RUNTIME_DIR:-/tmp/vaudeville-$(id -u)}"
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
if [ ! -d "${MODEL_CACHE}" ]; then
  echo "[vaudeville] Model not downloaded (${BACKEND}). Run /vaudeville:setup to download it." >&2
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
    for _ in $(seq 1 20); do
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

# Atomic spawn lock — mkdir is atomic on macOS and Linux.
# Prevents thundering herd when multiple sessions start concurrently.
# Install the cleanup trap ONLY after we successfully create the lock,
# so that a failed mkdir (another process holds the lock) does not
# remove the other process's lock directory on exit.
SPAWN_LOCK="${RUNTIME_DIR}/spawn.lock"
if ! mkdir "${SPAWN_LOCK}" 2>/dev/null; then
  echo "[vaudeville] Another session is spawning the daemon — skipping" >&2
  exit 0
fi
cleanup_spawn_lock() { rm -rf "${SPAWN_LOCK}" 2>/dev/null; }
trap cleanup_spawn_lock EXIT

# Rotate log if larger than 50 MB (keeps one rotated sibling as .log.1)
if [ -f "${LOG_FILE}" ]; then
  _log_size=$(wc -c < "${LOG_FILE}")
  if [ "${_log_size}" -gt $((50 * 1024 * 1024)) ]; then
    mv "${LOG_FILE}" "${LOG_FILE}.1"
    echo "[vaudeville] Log rotated (was ${_log_size} bytes) — old log at ${LOG_FILE}.1" >&2
  fi
fi

# Spawn daemon with platform-appropriate backend and deps
nohup uv run --project "${PLUGIN_ROOT}" --group "${DEP_GROUP}" \
  python -m vaudeville.server \
  --socket "${SOCKET_PATH}" \
  --pid-file "${PID_FILE}" \
  --backend "${BACKEND}" \
  >> "${LOG_FILE}" 2>&1 &
DAEMON_PID=$!
disown "${DAEMON_PID}" 2>/dev/null || true

echo "[vaudeville] Daemon spawned (${BACKEND}, PID ${DAEMON_PID}, socket ${SOCKET_PATH})" >&2

# Poll for socket to appear (30 x 0.1s = 3s) — detect silent spawn failures
_socket_up=0
for _ in $(seq 1 30); do
  if [ -S "${SOCKET_PATH}" ]; then
    _socket_up=1
    break
  fi
  sleep 0.1
done
if [ "${_socket_up}" -eq 0 ]; then
  echo "[vaudeville] WARNING: daemon did not come up within 3s — check ${LOG_FILE} for errors" >&2
fi

export_socket_path
exit 0
