#!/usr/bin/env bash
# Run session-analytics skill if claude is available; fail quiet if not.
claude -p --skip-permissions "/session-analytics" 2>/dev/null || echo ''
