"""Tests for skills/session-analytics/scripts/queries/ helper modules."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from unittest.mock import patch

import pytest

# --- Module loading ---
# The query scripts use `from _db import query`, so we add the queries
# directory to sys.path and load each module by spec.

_QUERIES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills",
    "session-analytics",
    "scripts",
    "queries",
)

if _QUERIES_DIR not in sys.path:
    sys.path.insert(0, _QUERIES_DIR)


def _load(name: str):
    path = os.path.join(_QUERIES_DIR, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


_db = _load("_db")
denied_tools = _load("denied_tools")
tool_usage = _load("tool_usage")
error_rates = _load("error_rates")
bash_patterns = _load("bash_patterns")
tool_misuse = _load("tool_misuse")
hook_stats = _load("hook_stats")


def _mock_result(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return type(
        "R", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr}
    )()


# ── _db.query ──


class TestDbQuery:
    def test_db_not_found_exits(self, tmp_path) -> None:
        with patch.object(_db, "DB_PATH", str(tmp_path / "missing.duckdb")):
            with pytest.raises(SystemExit) as exc:
                _db.query("SELECT 1")
            assert exc.value.code == 1

    def test_duckdb_binary_not_found_exits(self, tmp_path) -> None:
        db = tmp_path / "test.duckdb"
        db.touch()
        with patch.object(_db, "DB_PATH", str(db)):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                with pytest.raises(SystemExit) as exc:
                    _db.query("SELECT 1")
                assert exc.value.code == 1

    def test_nonzero_returncode_returns_empty(self, tmp_path) -> None:
        db = tmp_path / "test.duckdb"
        db.touch()
        with patch.object(_db, "DB_PATH", str(db)):
            with patch(
                "subprocess.run", return_value=_mock_result(returncode=1, stderr="err")
            ):
                assert _db.query("SELECT 1") == []

    def test_empty_stdout_returns_empty(self, tmp_path) -> None:
        db = tmp_path / "test.duckdb"
        db.touch()
        with patch.object(_db, "DB_PATH", str(db)):
            with patch("subprocess.run", return_value=_mock_result(stdout="")):
                assert _db.query("SELECT 1") == []

    def test_duckdb_empty_sentinel_returns_empty(self, tmp_path) -> None:
        db = tmp_path / "test.duckdb"
        db.touch()
        with patch.object(_db, "DB_PATH", str(db)):
            with patch("subprocess.run", return_value=_mock_result(stdout="[{]")):
                assert _db.query("SELECT 1") == []

    def test_invalid_json_returns_empty(self, tmp_path) -> None:
        db = tmp_path / "test.duckdb"
        db.touch()
        with patch.object(_db, "DB_PATH", str(db)):
            with patch("subprocess.run", return_value=_mock_result(stdout="not-json")):
                assert _db.query("SELECT 1") == []

    def test_valid_json_returned(self, tmp_path) -> None:
        db = tmp_path / "test.duckdb"
        db.touch()
        data = [{"tool": "Bash", "cnt": "5"}]
        with patch.object(_db, "DB_PATH", str(db)):
            with patch(
                "subprocess.run", return_value=_mock_result(stdout=json.dumps(data))
            ):
                assert _db.query("SELECT 1") == data


# ── _db.parse_days / parse_limit ──


class TestArgParsers:
    def test_parse_days_default(self) -> None:
        assert _db.parse_days([]) == 14

    def test_parse_days_custom(self) -> None:
        assert _db.parse_days(["--days", "7"]) == 7

    def test_parse_days_custom_default(self) -> None:
        assert _db.parse_days([], default=30) == 30

    def test_parse_limit_default(self) -> None:
        assert _db.parse_limit([]) == 15

    def test_parse_limit_custom(self) -> None:
        assert _db.parse_limit(["--limit", "5"]) == 5

    def test_parse_days_missing_value_returns_default(self) -> None:
        assert _db.parse_days(["--days"]) == 14


# ── _db.output ──


class TestOutput:
    def test_json_mode(self, capsys) -> None:
        rows = [{"tool": "Bash", "uses": "10"}]
        _db.output(rows, ["--json"])
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed == rows

    def test_tsv_mode(self, capsys) -> None:
        rows = [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
        _db.output(rows, [])
        lines = capsys.readouterr().out.strip().split("\n")
        assert lines[0] == "a\tb"
        assert lines[1] == "1\t2"
        assert lines[2] == "3\t4"

    def test_empty_rows_prints_no_results(self, capsys) -> None:
        _db.output([], [])
        assert "(no results)" in capsys.readouterr().out


# ── denied_tools ──
# Scripts do `from _db import query`, so we must patch on the script module.


class TestDeniedTools:
    def test_queries_permission_denials_table(self) -> None:
        with patch.object(denied_tools, "query", return_value=[]) as mock_q:
            with patch("sys.argv", ["denied_tools.py"]):
                denied_tools.main()
        sql = mock_q.call_args[0][0]
        assert "permission_denials" in sql
        assert "regexp_extract" in sql

    def test_passes_days_and_limit(self) -> None:
        with patch.object(denied_tools, "query", return_value=[]) as mock_q:
            with patch("sys.argv", ["denied_tools.py", "--days", "7", "--limit", "5"]):
                denied_tools.main()
        sql = mock_q.call_args[0][0]
        assert "'7'" in sql
        assert "5" in sql

    def test_output_json(self, capsys) -> None:
        rows = [{"tool": "Bash", "denials": "10"}]
        with patch.object(denied_tools, "query", return_value=rows):
            with patch("sys.argv", ["denied_tools.py", "--json"]):
                denied_tools.main()
        parsed = json.loads(capsys.readouterr().out)
        assert parsed[0]["tool"] == "Bash"
        assert parsed[0]["denials"] == "10"


# ── tool_usage ──


class TestToolUsage:
    def test_queries_tool_uses_table(self) -> None:
        with patch.object(tool_usage, "query", return_value=[]) as mock_q:
            with patch("sys.argv", ["tool_usage.py"]):
                tool_usage.main()
        sql = mock_q.call_args[0][0]
        assert "tool_uses" in sql
        assert "tool_name" in sql

    def test_output_includes_sessions_column(self, capsys) -> None:
        rows = [{"tool_name": "Read", "uses": "50", "sessions": "10"}]
        with patch.object(tool_usage, "query", return_value=rows):
            with patch("sys.argv", ["tool_usage.py", "--json"]):
                tool_usage.main()
        parsed = json.loads(capsys.readouterr().out)
        assert parsed[0]["sessions"] == "10"


# ── error_rates ──


class TestErrorRates:
    def test_queries_tool_uses_join_results(self) -> None:
        with patch.object(error_rates, "query", return_value=[]) as mock_q:
            with patch("sys.argv", ["error_rates.py"]):
                error_rates.main()
        sql = mock_q.call_args[0][0]
        assert "tool_uses tu" in sql
        assert "tool_results tr" in sql
        assert "error_pct" in sql

    def test_min_uses_flag(self) -> None:
        with patch.object(error_rates, "query", return_value=[]) as mock_q:
            with patch("sys.argv", ["error_rates.py", "--min-uses", "20"]):
                error_rates.main()
        sql = mock_q.call_args[0][0]
        assert "20" in sql

    def test_parse_min_uses_default(self) -> None:
        assert error_rates.parse_min_uses([]) == 5


# ── bash_patterns ──


class TestBashPatterns:
    def test_normal_mode_no_danger_filter(self) -> None:
        with patch.object(bash_patterns, "query", return_value=[]) as mock_q:
            with patch("sys.argv", ["bash_patterns.py"]):
                bash_patterns.main()
        sql = mock_q.call_args[0][0]
        assert "rm -rf" not in sql

    def test_dangerous_mode_adds_filter(self) -> None:
        with patch.object(bash_patterns, "query", return_value=[]) as mock_q:
            with patch("sys.argv", ["bash_patterns.py", "--dangerous"]):
                bash_patterns.main()
        sql = mock_q.call_args[0][0]
        assert "rm -rf" in sql
        assert "--no-verify" in sql
        assert "chmod 777" in sql


# ── tool_misuse ──


class TestToolMisuse:
    def test_queries_misuse_patterns(self) -> None:
        with patch.object(tool_misuse, "query", return_value=[]) as mock_q:
            with patch("sys.argv", ["tool_misuse.py"]):
                tool_misuse.main()
        sql = mock_q.call_args[0][0]
        assert "cat" in sql.lower()
        assert "grep" in sql.lower()
        assert "find" in sql.lower()
        assert "sed" in sql.lower()

    def test_output_has_misuse_type(self, capsys) -> None:
        rows = [{"misuse_type": "grep/rg -> Grep", "uses": "100"}]
        with patch.object(tool_misuse, "query", return_value=rows):
            with patch("sys.argv", ["tool_misuse.py", "--json"]):
                tool_misuse.main()
        parsed = json.loads(capsys.readouterr().out)
        assert "Grep" in parsed[0]["misuse_type"]


# ── hook_stats ──


class TestHookStats:
    def _mock_three_queries(self, hook_cnt, stop_cnt, errors=None):
        """Return a side_effect for the 3 queries hook_stats.main() makes."""
        returns = [
            [{"cnt": str(hook_cnt)}],
            [{"cnt": str(stop_cnt)}],
            errors or [],
        ]
        call_idx = [0]

        def _q(sql):  # noqa: ARG001
            idx = call_idx[0]
            call_idx[0] += 1
            return returns[idx]

        return _q

    def test_coverage_calculation(self, capsys) -> None:
        with patch.object(
            hook_stats, "query", side_effect=self._mock_three_queries(50, 100)
        ):
            with patch("sys.argv", ["hook_stats.py"]):
                hook_stats.main()
        out = capsys.readouterr().out
        assert "50.0%" in out

    def test_zero_stops_no_crash(self, capsys) -> None:
        with patch.object(
            hook_stats, "query", side_effect=self._mock_three_queries(0, 0)
        ):
            with patch("sys.argv", ["hook_stats.py"]):
                hook_stats.main()
        out = capsys.readouterr().out
        assert "0%" in out

    def test_json_output_structure(self, capsys) -> None:
        errors = [{"error": "Timeout", "cnt": "3"}]
        with patch.object(
            hook_stats, "query", side_effect=self._mock_three_queries(30, 100, errors)
        ):
            with patch("sys.argv", ["hook_stats.py", "--json"]):
                hook_stats.main()
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["stop_events"] == 100
        assert parsed["hook_executions"] == 30
        assert parsed["coverage_pct"] == 30.0
        assert parsed["errors"][0]["error"] == "Timeout"

    def test_errors_shown_in_text_mode(self, capsys) -> None:
        errors = [{"error": "Script crashed", "cnt": "5"}]
        with patch.object(
            hook_stats, "query", side_effect=self._mock_three_queries(10, 50, errors)
        ):
            with patch("sys.argv", ["hook_stats.py"]):
                hook_stats.main()
        out = capsys.readouterr().out
        assert "Script crashed" in out
        assert "5x" in out

    def test_days_flag_passed_to_queries(self) -> None:
        sqls = []
        call_idx = [0]

        def capture_sql(sql):
            sqls.append(sql)
            call_idx[0] += 1
            if call_idx[0] <= 2:
                return [{"cnt": "0"}]
            return []  # errors query returns no rows

        with patch.object(hook_stats, "query", side_effect=capture_sql):
            with patch("sys.argv", ["hook_stats.py", "--days", "7"]):
                hook_stats.main()
        for sql in sqls:
            assert "'7'" in sql
