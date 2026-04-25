#!/bin/bash
set -euo pipefail
# Resolve to project root — ralph runs ./scripts from the ralph_dir.
cd "$(dirname "$0")/../.."

RULE_NAME="${1:-}"
if [ -z "$RULE_NAME" ]; then
    echo "Usage: read-rule.sh <rule_name>" >&2
    exit 1
fi

RULES_DIR="${VAUDEVILLE_RULES_DIR:-.vaudeville/rules}"
if [ -f "$RULES_DIR/$RULE_NAME.yaml" ]; then
    cat "$RULES_DIR/$RULE_NAME.yaml"
elif [ -f "$RULES_DIR/$RULE_NAME.yml" ]; then
    cat "$RULES_DIR/$RULE_NAME.yml"
elif [ -f "examples/rules/$RULE_NAME.yaml" ]; then
    cat "examples/rules/$RULE_NAME.yaml"
elif [ -f "examples/rules/$RULE_NAME.yml" ]; then
    cat "examples/rules/$RULE_NAME.yml"
else
    echo "(no rule file found for $RULE_NAME)"
fi
