#!/bin/bash
set -euo pipefail

RULE_NAME="${1:-}"
if [ -z "$RULE_NAME" ]; then
    # Try to find the most recently modified rule
    RULE_NAME=$(ls -t .vaudeville/rules/*.yaml 2>/dev/null | head -n1 | xargs -I{} basename {} .yaml || echo "")
fi

if [ -z "$RULE_NAME" ]; then
    echo "No rule specified and no rules found in .vaudeville/rules/" >&2
    exit 1
fi

uv run python -m vaudeville.eval_cli --rule "$RULE_NAME" 2>&1 | tail -n 100
