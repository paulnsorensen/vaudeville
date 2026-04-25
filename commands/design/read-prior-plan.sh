#!/bin/bash
set -euo pipefail
# Resolve to project root — ralph runs ./scripts from the ralph_dir.
cd "$(dirname "$0")/../.."

RULE_NAME="${1:-}"
if [ -z "$RULE_NAME" ]; then
    echo "Usage: read-prior-plan.sh <rule_name>" >&2
    exit 1
fi

PLAN_FILE="commands/tune/state/$RULE_NAME.plan.md"
if [ -f "$PLAN_FILE" ]; then
    cat "$PLAN_FILE"
else
    echo "EMPTY_PLAN"
fi
