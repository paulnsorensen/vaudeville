#!/usr/bin/env bash
# Emit project-scoped session patterns for the designer. Fail-open.
uv run python -c "
from vaudeville.analytics import query_session_patterns
import os
print(query_session_patterns(os.environ.get('VAUDEVILLE_PROJECT_CWD')))
" 2>/dev/null || echo ""
