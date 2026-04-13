#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Checklist of implementation items — grep for markers in code
items=(
  "ClassifyRequest.rule field:vaudeville/core/protocol.py:rule.*str"
  "Client rule param:vaudeville/core/client.py:def classify.*rule"
  "Runner passes rule name:hooks/runner.py:classify.*rule"
  "Log config loader:vaudeville/server/event_log.py:config"
  "EventLogger class:vaudeville/server/event_log.py:class EventLogger"
  "EventLogger wired in daemon:vaudeville/server/daemon.py:event_log|EventLogger"
  "Stats aggregation:vaudeville/server/stats.py:aggregate_events"
  "Stats CLI command:vaudeville/__main__.py:stats"
  "Watch TUI module:vaudeville/server/watch.py:rich"
  "Watch CLI command:vaudeville/__main__.py:watch"
  "Loguru in pyproject:pyproject.toml:loguru"
  "Rich in pyproject:pyproject.toml:rich"
)

total=${#items[@]}
done=0

echo "=== Observability Progress ==="
for item in "${items[@]}"; do
  IFS=: read -r label file pattern <<< "$item"
  if [ -f "$file" ] && grep -qE "$pattern" "$file" 2>/dev/null; then
    echo "  [x] $label"
    ((done++))
  else
    echo "  [ ] $label"
  fi
done

echo ""
echo "$done/$total items complete"

if [ "$done" -eq "$total" ]; then
  # Also verify tests pass
  if uv run pytest --tb=no -q 2>&1 | tail -1 | grep -q "passed"; then
    echo ""
    echo "ALL COMPLETE"
  else
    echo ""
    echo "All items present but tests are failing — fix tests."
  fi
fi
