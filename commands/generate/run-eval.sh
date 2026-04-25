#!/bin/bash
set -euo pipefail

RULES_DIR="${VAUDEVILLE_RULES_DIR:-.vaudeville/rules}"
RULE_NAME="${1:-}"
if [ -z "$RULE_NAME" ]; then
    latest=$(ls -t "$RULES_DIR"/*.yaml "$RULES_DIR"/*.yml 2>/dev/null | head -n1 || true)
    if [ -n "$latest" ]; then
        RULE_NAME=$(basename "$latest")
        RULE_NAME="${RULE_NAME%.yaml}"
        RULE_NAME="${RULE_NAME%.yml}"
    fi
fi

if [ -z "$RULE_NAME" ]; then
    echo "No rule specified and no rules found in $RULES_DIR" >&2
    exit 1
fi

uv run python -m vaudeville.eval_cli --rule "$RULE_NAME" 2>&1 | tail -n 100
