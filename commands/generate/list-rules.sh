#!/bin/bash
# List files in the rules dir; print "empty" if the dir is missing
# or contains no rules. Extracted from RALPH.md frontmatter because ralph
# runs commands via shlex.split + subprocess (no shell), which mangles
# `2>/dev/null || echo ...` into literal args.
set -euo pipefail
# Resolve to project root — ralph runs ./scripts from the ralph_dir.
cd "$(dirname "$0")/../.."

RULES_DIR="${VAUDEVILLE_RULES_DIR:-.vaudeville/rules}"
if [ -d "$RULES_DIR" ] && [ -n "$(ls -A "$RULES_DIR" 2>/dev/null)" ]; then
    ls -la "$RULES_DIR/"
else
    echo "empty"
fi
