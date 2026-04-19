#!/bin/bash
set -euo pipefail

shopt -s nullglob
count=0
for f in .vaudeville/rules/*.yaml; do
    if grep -q "^tier: shadow" "$f" 2>/dev/null; then
        count=$((count + 1))
    fi
done
echo "$count"
