#!/bin/bash
set -euo pipefail

RULE_NAME="${1:-}"
if [ -z "$RULE_NAME" ]; then
    echo "Usage: read-rule.sh <rule_name>" >&2
    exit 1
fi

if [ -f ".vaudeville/rules/$RULE_NAME.yaml" ]; then
    cat ".vaudeville/rules/$RULE_NAME.yaml"
elif [ -f "examples/rules/$RULE_NAME.yaml" ]; then
    cat "examples/rules/$RULE_NAME.yaml"
else
    echo "(no rule file found for $RULE_NAME)"
fi
