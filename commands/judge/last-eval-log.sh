#!/bin/bash
set -euo pipefail

RULE_NAME="${1:-}"
if [ -z "$RULE_NAME" ]; then
    echo "Usage: last-eval-log.sh <rule_name>" >&2
    exit 1
fi

LOG_FILE=".vaudeville/logs/eval-$RULE_NAME.log"
if [ -f "$LOG_FILE" ]; then
    cat "$LOG_FILE"
else
    echo "(no eval log found for $RULE_NAME)"
fi
