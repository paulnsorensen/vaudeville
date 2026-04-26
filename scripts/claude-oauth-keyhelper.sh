#!/usr/bin/env bash
# apiKeyHelper for vaudeville ralph subprocesses.
#
# Extracts the Claude Code OAuth accessToken from the macOS keychain and
# emits it on stdout so `claude -p --bare --settings '{"apiKeyHelper":...}'`
# can authenticate without ANTHROPIC_API_KEY. This lets ralph phases run
# with full --bare isolation (no hooks, plugins, CLAUDE.md walk-up, settings)
# while still using the user's existing OAuth subscription.

set -euo pipefail
security find-generic-password -s "Claude Code-credentials" -w \
  | jq -r '.claudeAiOauth.accessToken'
