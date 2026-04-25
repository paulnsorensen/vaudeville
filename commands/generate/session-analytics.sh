#!/usr/bin/env bash
# Emit project-scoped session patterns for the designer. Fail-open.
# Resolve to project root — ralph runs ./scripts from the ralph_dir.
cd "$(dirname "$0")/../.." || exit 0
uv run python -c "
from vaudeville.analytics import query_session_patterns
import os
print(query_session_patterns(os.environ.get('VAUDEVILLE_PROJECT_CWD')))
" 2>/dev/null || echo ""
