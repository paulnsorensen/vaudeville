#!/usr/bin/env bash
# Wraps `uv run` for command hooks so the daemon's venv lives in
# ${CLAUDE_PLUGIN_DATA} (persists across plugin updates) instead of
# ${CLAUDE_PLUGIN_ROOT} (changes on every update, forcing a fresh dependency
# resolve). Falls back to uv's own default project-local venv when
# CLAUDE_PLUGIN_DATA is not set.
set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

if [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
  export UV_PROJECT_ENVIRONMENT="${CLAUDE_PLUGIN_DATA}/venv"
fi

# Fail open: runner.py always exits 0 for claude-code, so a nonzero exit here
# is uv's own failure. Claude Code reads exit 2 as a block; never pass it on.
command -v uv >/dev/null 2>&1 || exit 0
uv run --project "${PLUGIN_ROOT}" python "${PLUGIN_ROOT}/hooks/runner.py" "$@" || exit 0
