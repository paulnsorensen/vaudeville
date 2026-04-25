#!/bin/bash
set -euo pipefail
# Resolve to project root — ralph runs ./scripts from the ralph_dir.
cd "$(dirname "$0")/../.."

RULE_NAME="${1:-}"
if [ -z "$RULE_NAME" ]; then
    echo "Usage: read-tune-results.sh <rule_name>" >&2
    exit 1
fi

HEADER="commit	precision	recall	f1	status	description"

if [ ! -f "tune-results.tsv" ]; then
    echo "$HEADER"
    echo "(no rows for $RULE_NAME)"
    exit 0
fi

echo "$HEADER"
ROWS=$(grep -F "$RULE_NAME" "tune-results.tsv" || true)
if [ -z "$ROWS" ]; then
    echo "(no rows for $RULE_NAME)"
else
    printf '%s\n' "$ROWS"
fi
