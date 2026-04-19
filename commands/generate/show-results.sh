#!/bin/bash
set -euo pipefail

if [ -f generate-results.tsv ]; then
    cat generate-results.tsv
else
    echo "No generate-results.tsv yet"
fi
