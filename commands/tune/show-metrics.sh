#!/bin/bash
set -euo pipefail

if [ -f tune-results.tsv ]; then
    cat tune-results.tsv
    echo ""
    tail -n 1 tune-results.tsv | awk -F'\t' '{printf "Precision: %s\nRecall: %s\nF1: %s\nStatus: %s\n", $2, $3, $4, $5}'
else
    echo "No tune-results.tsv found. Run baseline first."
fi
