#!/bin/bash
# List files in .vaudeville/rules/; print "empty" if the dir is missing
# or contains no rules. Extracted from RALPH.md frontmatter because ralph
# runs commands via shlex.split + subprocess (no shell), which mangles
# `2>/dev/null || echo ...` into literal args.
set -euo pipefail

if [ -d .vaudeville/rules ] && [ -n "$(ls -A .vaudeville/rules 2>/dev/null)" ]; then
    ls -la .vaudeville/rules/
else
    echo "empty"
fi
