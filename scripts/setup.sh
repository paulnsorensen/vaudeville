#!/usr/bin/env bash
# Download the Phi-4-mini model required for inference.
# Run once after installing the plugin.
set -euo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
exec uv run --project "${PLUGIN_ROOT}" python -m vaudeville.setup
