"""DuckDB session log ingester — Python API version.

Adapted from the session-analytics skill's ingest.py.
Replaces subprocess duckdb CLI with the duckdb Python binding.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb


def build_database(db_path: Path, jsonl_glob: str) -> None:
    """Build or rebuild the sessions DuckDB from JSONL logs.

    Writes to a tmp file first; atomically replaces the live DB on success.
    """
    tmp_path = db_path.parent / f"sessions.duckdb.{os.getpid()}.tmp"
    if tmp_path.exists():
        tmp_path.unlink()

    con = duckdb.connect(str(tmp_path))
    try:
        _create_raw_entries(con, jsonl_glob)
        _create_tool_uses(con)
        _create_tool_results(con)
        _create_stop_events(con)
        _create_agent_spawns(con)
        _create_skill_invocations(con)
        _create_mcp_calls(con)
        _create_sessions(con)
        _create_stop_hooks(con)
        _create_permission_denials(con)
        _create_indexes(con)
    finally:
        con.close()

    os.replace(tmp_path, db_path)


def _create_raw_entries(con: duckdb.DuckDBPyConnection, jsonl_glob: str) -> None:
    escaped_glob = jsonl_glob.replace("'", "''")
    con.execute(f"""
        CREATE TABLE raw_entries AS
        SELECT *
        FROM read_json(
            '{escaped_glob}',
            format='newline_delimited',
            union_by_name=true,
            ignore_errors=true,
            columns={{
                type: 'VARCHAR',
                subtype: 'VARCHAR',
                timestamp: 'VARCHAR',
                sessionId: 'VARCHAR',
                uuid: 'VARCHAR',
                parentUuid: 'VARCHAR',
                message: 'JSON',
                version: 'VARCHAR',
                gitBranch: 'VARCHAR',
                slug: 'VARCHAR',
                cwd: 'VARCHAR',
                hookCount: 'INTEGER',
                hookInfos: 'JSON',
                hookErrors: 'JSON',
                preventedContinuation: 'BOOLEAN',
                stopReason: 'VARCHAR',
                hasOutput: 'BOOLEAN',
                level: 'VARCHAR',
                isSidechain: 'BOOLEAN',
                userType: 'VARCHAR',
                filename: 'VARCHAR'
            }}
        );
    """)


def _create_tool_uses(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE tool_uses AS
        WITH content_blocks AS (
            SELECT
                unnest(json_extract(json_extract(message, '$.content'), '$[*]')) AS block,
                timestamp,
                sessionId,
                cwd,
                gitBranch
            FROM raw_entries
            WHERE type = 'assistant'
              AND message IS NOT NULL
              AND json_extract(message, '$.content') IS NOT NULL
              AND json_type(json_extract(message, '$.content')) = 'ARRAY'
        )
        SELECT
            json_extract_string(block, '$.name') AS tool_name,
            json_extract_string(block, '$.id') AS tool_use_id,
            json_extract(block, '$.input') AS input,
            json_extract_string(block, '$.input.command') AS bash_cmd,
            json_extract_string(block, '$.input.skill') AS skill_name,
            json_extract_string(block, '$.input.args') AS skill_args,
            json_extract_string(block, '$.input.subagent_type') AS agent_type,
            json_extract_string(block, '$.input.description') AS agent_desc,
            json_extract_string(block, '$.input.mode') AS agent_mode,
            json_extract_string(block, '$.input.pattern') AS grep_pattern,
            json_extract_string(block, '$.input.file_path') AS file_path,
            json_extract_string(block, '$.input.query') AS query,
            timestamp,
            sessionId,
            cwd,
            gitBranch
        FROM content_blocks
        WHERE json_extract_string(block, '$.type') = 'tool_use';
    """)


def _create_tool_results(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE tool_results AS
        WITH content_blocks AS (
            SELECT
                unnest(json_extract(json_extract(message, '$.content'), '$[*]')) AS block,
                timestamp,
                sessionId
            FROM raw_entries
            WHERE type = 'user'
              AND message IS NOT NULL
              AND json_type(json_extract(message, '$.content')) = 'ARRAY'
        )
        SELECT
            json_extract_string(block, '$.tool_use_id') AS tool_use_id,
            substr(json_extract_string(block, '$.content'), 1, 500) AS content,
            json_extract_string(block, '$.is_error') AS is_error,
            timestamp,
            sessionId
        FROM content_blocks
        WHERE json_extract_string(block, '$.type') = 'tool_result';
    """)


def _create_stop_events(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE stop_events AS
        SELECT
            json_extract_string(message, '$.stop_reason') AS stop_reason,
            timestamp,
            sessionId,
            cwd,
            gitBranch
        FROM raw_entries
        WHERE type = 'assistant'
          AND message IS NOT NULL
          AND json_extract_string(message, '$.stop_reason')
              IN ('end_turn', 'stop_sequence', 'max_tokens');
    """)


def _create_agent_spawns(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE agent_spawns AS
        SELECT
            coalesce(agent_type, 'general-purpose') AS agent_type,
            agent_desc AS description,
            agent_mode AS mode,
            timestamp,
            sessionId,
            cwd
        FROM tool_uses
        WHERE tool_name = 'Agent';
    """)


def _create_skill_invocations(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE skill_invocations AS
        SELECT
            skill_name,
            skill_args AS args,
            timestamp,
            sessionId,
            cwd
        FROM tool_uses
        WHERE tool_name = 'Skill';
    """)


def _create_mcp_calls(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE mcp_calls AS
        SELECT *
        FROM tool_uses
        WHERE tool_name LIKE 'mcp__%';
    """)


def _create_sessions(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE sessions AS
        SELECT
            sessionId,
            min(timestamp) AS first_seen,
            max(timestamp) AS last_seen,
            cwd AS project,
            gitBranch AS branch,
            count(*) AS entry_count
        FROM raw_entries
        WHERE sessionId IS NOT NULL
          AND timestamp IS NOT NULL
        GROUP BY sessionId, cwd, gitBranch;
    """)


def _create_stop_hooks(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE stop_hooks AS
        SELECT
            timestamp,
            sessionId,
            hookCount,
            hookInfos,
            hookErrors,
            preventedContinuation,
            stopReason,
            hasOutput,
            level
        FROM raw_entries
        WHERE type = 'system'
          AND subtype = 'stop_hook_summary';
    """)


def _create_permission_denials(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE permission_denials AS
        SELECT
            content,
            sessionId,
            timestamp
        FROM tool_results
        WHERE content LIKE 'Permission to use % has been denied%'
           OR content LIKE 'Hook PreToolUse:% denied this tool%'
           OR content LIKE '%The user doesn''t want to proceed%';
    """)


def _create_indexes(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE INDEX idx_tool_uses_name ON tool_uses(tool_name);
        CREATE INDEX idx_tool_uses_session ON tool_uses(sessionId);
        CREATE INDEX idx_tool_results_error ON tool_results(is_error);
        CREATE INDEX idx_sessions_id ON sessions(sessionId);
    """)
