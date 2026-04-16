#!/bin/bash
set -euo pipefail

# Show current metrics from tune-results.tsv
if [ -f tune-results.tsv ]; then
    echo "=== Tuning History ==="
    cat tune-results.tsv
    echo ""
    echo "=== Latest Metrics ==="
    tail -n 1 tune-results.tsv | awk -F'\t' '{printf "Precision: %s\nRecall: %s\nF1: %s\nStatus: %s\n", $2, $3, $4, $5}'
else
    echo "No tune-results.tsv found. Run baseline first."
fi
