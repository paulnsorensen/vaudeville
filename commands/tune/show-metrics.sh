#!/bin/bash
set -euo pipefail
# Resolve to project root — ralph runs ./scripts from the ralph_dir.
cd "$(dirname "$0")/../.."

RULE_NAME="${1:-}"

if [ ! -f "tune-results.tsv" ]; then
    echo "No tune-results.tsv found. Run baseline first."
    exit 0
fi

cat "tune-results.tsv"
echo ""

if [ -n "$RULE_NAME" ]; then
    LAST_ROW=$(grep -F "$RULE_NAME" "tune-results.tsv" | tail -n 1 || true)
else
    LAST_ROW=$(tail -n 1 "tune-results.tsv")
fi

if [ -n "$LAST_ROW" ]; then
    printf '%s\n' "$LAST_ROW" | awk -F'\t' '{printf "Precision: %s\nRecall: %s\nF1: %s\nStatus: %s\n", $2, $3, $4, $5}'
fi
