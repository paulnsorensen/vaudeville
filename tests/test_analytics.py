"""Tests for the vaudeville.analytics module.

Covers DB ingestion, TTL skipping, and session pattern queries.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_jsonl(path: Path, records: list[dict]) -> None:  # type: ignore[type-arg]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _make_tool_use_entry(
    session_id: str,
    cwd: str,
    tool_name: str,
    bash_cmd: str | None = None,
    timestamp: str = "2024-01-01T00:00:00Z",
) -> dict:  # type: ignore[type-arg]
    input_obj: dict = {}  # type: ignore[type-arg]
    if bash_cmd:
        input_obj["command"] = bash_cmd
    return {
        "type": "assistant",
        "sessionId": session_id,
        "cwd": cwd,
        "gitBranch": "main",
        "timestamp": timestamp,
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": tool_name,
                    "id": f"tu-{session_id}",
                    "input": input_obj,
                }
            ]
        },
    }


def _make_denial_entry(
    session_id: str,
    tool_use_id: str,
    timestamp: str = "2024-01-01T00:00:01Z",
) -> dict:  # type: ignore[type-arg]
    return {
        "type": "user",
        "sessionId": session_id,
        "timestamp": timestamp,
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": "Permission to use Bash has been denied",
                }
            ]
        },
    }


@pytest.fixture
def analytics_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect analytics module to tmp_path for isolation."""
    db_path = tmp_path / "sessions.duckdb"
    jsonl_dir = tmp_path / "projects"
    jsonl_dir.mkdir()
    monkeypatch.setattr("vaudeville.analytics._DB_PATH", db_path)
    monkeypatch.setattr("vaudeville.analytics._DB_DIR", tmp_path)
    monkeypatch.setattr(
        "vaudeville.analytics._JSONL_GLOB",
        str(jsonl_dir / "**" / "*.jsonl"),
    )
    return tmp_path


class TestIngest:
    def test_ingest_creates_tables(self, analytics_env: Path) -> None:
        """ingest() builds DB with all 10 required tables."""
        import duckdb

        from vaudeville.analytics import ingest

        jsonl_file = analytics_env / "projects" / "alpha" / "session.jsonl"
        _write_jsonl(
            jsonl_file,
            [
                _make_tool_use_entry("sess1", "/alpha", "Bash", "git status"),
                _make_denial_entry("sess1", "tu-sess1"),
                {
                    "type": "system",
                    "subtype": "stop_hook_summary",
                    "sessionId": "sess1",
                    "timestamp": "2024-01-01T00:00:02Z",
                    "hookCount": 0,
                    "hookInfos": [],
                    "hookErrors": [],
                    "preventedContinuation": False,
                    "stopReason": "end_turn",
                    "hasOutput": False,
                    "level": "info",
                },
            ],
        )

        db_path = ingest(force=True)

        expected_tables = {
            "raw_entries",
            "tool_uses",
            "tool_results",
            "stop_events",
            "agent_spawns",
            "skill_invocations",
            "mcp_calls",
            "sessions",
            "stop_hooks",
            "permission_denials",
        }
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            actual = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        finally:
            con.close()

        assert expected_tables.issubset(actual)

    def test_ingest_ttl_skips_rebuild(self, analytics_env: Path) -> None:
        """Second ingest() within TTL does not rebuild the DB."""
        from vaudeville.analytics import ingest

        jsonl_file = analytics_env / "projects" / "p" / "s.jsonl"
        _write_jsonl(jsonl_file, [_make_tool_use_entry("s1", "/p", "Read")])

        with patch("vaudeville.analytics._ingest.build_database") as mock_build:

            def create_file(db_path: Path, jsonl_glob: str) -> None:
                db_path.touch()

            mock_build.side_effect = create_file

            ingest(force=True)
            ingest()

        assert mock_build.call_count == 1


class TestQuerySessionPatterns:
    def test_query_session_patterns_no_filter(self, analytics_env: Path) -> None:
        """query_session_patterns() returns a non-empty string."""
        from vaudeville.analytics import ingest, query_session_patterns

        jsonl_file = analytics_env / "projects" / "p" / "s.jsonl"
        _write_jsonl(
            jsonl_file,
            [_make_tool_use_entry("s1", "/projects/p", "Bash", "ls")],
        )
        ingest(force=True)

        result = query_session_patterns()

        # Output always contains these section headers
        assert "## Top bash commands" in result
        assert "## Top tool uses" in result
        # The ingested entry was a Bash tool_use with cmd "ls"
        assert "Bash" in result

    def test_query_session_patterns_with_filter(self, analytics_env: Path) -> None:
        """project_filter restricts results to matching cwd sessions."""
        from vaudeville.analytics import ingest, query_session_patterns

        jsonl_file = analytics_env / "projects" / "mixed" / "s.jsonl"
        _write_jsonl(
            jsonl_file,
            [
                _make_tool_use_entry(
                    "alpha-sess",
                    "/projects/alpha",
                    "Bash",
                    "ls /alpha-only",
                    timestamp="2024-01-01T00:00:00Z",
                ),
                _make_tool_use_entry(
                    "beta-sess",
                    "/projects/beta",
                    "Read",
                    timestamp="2024-01-01T00:00:01Z",
                ),
            ],
        )
        ingest(force=True)

        result = query_session_patterns("alpha")

        # Alpha session used Bash with "ls /alpha-only"; beta session used Read
        assert "Bash" in result
        assert "ls /alpha-only" in result
        assert "Read" not in result
