#!/bin/bash
set -euo pipefail

RULE_NAME="${1:-}"
if [ -z "$RULE_NAME" ]; then
    echo "Usage: run-eval.sh <rule_name>" >&2
    exit 1
fi

uv run python -m vaudeville.eval --rule "$RULE_NAME" 2>&1 | tail -n 100
