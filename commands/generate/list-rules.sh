#!/bin/bash
set -euo pipefail

if [ -d .vaudeville/rules ]; then
    ls -la .vaudeville/rules/
else
    echo "No rules directory"
fi
