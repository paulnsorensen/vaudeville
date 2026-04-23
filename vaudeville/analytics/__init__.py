"""Session analytics module.

Provides direct Python access to Claude Code session patterns via DuckDB.
Replaces the subprocess-based session-analytics skill invocation.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

_DB_DIR = Path.home() / ".claude" / "analytics"
_DB_PATH = _DB_DIR / "sessions.duckdb"
_JSONL_GLOB = str(Path.home() / ".claude" / "projects" / "**" / "*.jsonl")
_TTL_SECONDS = 3600


def ingest(force: bool = False) -> Path:
    """Build or refresh the sessions DuckDB (1-hour TTL). Returns path to DB."""
    db_path = _DB_PATH
    if not force and db_path.exists():
        age = time.time() - db_path.stat().st_mtime
        if age < _TTL_SECONDS:
            return db_path
    from vaudeville.analytics._ingest import build_database

    db_path.parent.mkdir(parents=True, exist_ok=True)
    build_database(db_path, _JSONL_GLOB)
    return db_path


def query_session_patterns(project_filter: str | None = None) -> str:
    """Return a compact text block of session patterns for the designer prompt.

    When project_filter is set, restricts results to sessions whose cwd contains it.
    """
    import duckdb

    db_path = ingest()
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return _build_patterns_text(con, project_filter)
    finally:
        con.close()


def _build_patterns_text(con: Any, project_filter: str | None) -> str:
    lines: list[str] = []

    cwd_filter = f"%{project_filter}%" if project_filter else None

    lines.append("## Top bash commands")
    if cwd_filter is not None:
        rows = con.execute(
            "SELECT bash_cmd, count(*) AS uses FROM tool_uses"
            " WHERE tool_name = 'Bash' AND bash_cmd IS NOT NULL AND cwd LIKE ?"
            " GROUP BY bash_cmd ORDER BY uses DESC LIMIT 10",
            [cwd_filter],
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT bash_cmd, count(*) AS uses FROM tool_uses"
            " WHERE tool_name = 'Bash' AND bash_cmd IS NOT NULL"
            " GROUP BY bash_cmd ORDER BY uses DESC LIMIT 10"
        ).fetchall()
    for cmd, count in rows:
        lines.append(f"  {count:4d}  {cmd}")

    lines.append("\n## Top tool uses")
    if cwd_filter is not None:
        rows = con.execute(
            "SELECT tool_name, count(*) AS uses FROM tool_uses"
            " WHERE cwd LIKE ?"
            " GROUP BY tool_name ORDER BY uses DESC LIMIT 10",
            [cwd_filter],
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT tool_name, count(*) AS uses FROM tool_uses"
            " GROUP BY tool_name ORDER BY uses DESC LIMIT 10"
        ).fetchall()
    for tool, count in rows:
        lines.append(f"  {count:4d}  {tool}")

    lines.append("\n## Permission denials")
    if cwd_filter is not None:
        rows = con.execute(
            "SELECT pd.content, count(*) AS cnt"
            " FROM permission_denials pd"
            " JOIN sessions s ON pd.sessionId = s.sessionId"
            " WHERE s.project LIKE ?"
            " GROUP BY pd.content ORDER BY cnt DESC LIMIT 5",
            [cwd_filter],
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT content, count(*) AS cnt FROM permission_denials"
            " GROUP BY content ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
    for content, count in rows:
        lines.append(f"  {count:4d}  {content[:80]}")

    return "\n".join(lines)
