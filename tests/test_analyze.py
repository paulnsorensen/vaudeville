"""Tests for skills/hook-suggester/scripts/analyze.py."""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
from typing import Any, Callable
from unittest.mock import patch

import pytest

# Load analyze.py as a module from the skills directory
_ANALYZE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills",
    "hook-suggester",
    "scripts",
    "analyze.py",
)

spec = importlib.util.spec_from_file_location("analyze", _ANALYZE_PATH)
assert spec is not None and spec.loader is not None
analyze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


class TestQuery:
    def test_duckdb_not_found_exits(self, tmp_path: pathlib.Path) -> None:
        db = str(tmp_path / "test.duckdb")
        with patch.object(analyze, "DB_PATH", db):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                with pytest.raises(SystemExit) as exc_info:
                    analyze.query("SELECT 1")
                assert exc_info.value.code == 1

    def test_nonzero_returncode_returns_empty(
        self, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
    ) -> None:
        db = str(tmp_path / "test.duckdb")
        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "err!"})()
        with patch.object(analyze, "DB_PATH", db):
            with patch("subprocess.run", return_value=mock_result):
                result = analyze.query("SELECT 1")
        assert result == []
        assert "WARNING" in capsys.readouterr().err

    def test_nonzero_with_stderr_prints_stderr(
        self, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
    ) -> None:
        db = str(tmp_path / "test.duckdb")
        mock_result = type(
            "R", (), {"returncode": 1, "stdout": "", "stderr": "SQL error"}
        )()
        with patch.object(analyze, "DB_PATH", db):
            with patch("subprocess.run", return_value=mock_result):
                analyze.query("BAD SQL")
        err = capsys.readouterr().err
        assert "SQL error" in err

    def test_empty_stdout_returns_empty(self, tmp_path: pathlib.Path) -> None:
        db = str(tmp_path / "test.duckdb")
        mock_result = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patch.object(analyze, "DB_PATH", db):
            with patch("subprocess.run", return_value=mock_result):
                result = analyze.query("SELECT 1")
        assert result == []

    def test_duckdb_empty_json_sentinel_returns_empty(
        self, tmp_path: pathlib.Path
    ) -> None:
        db = str(tmp_path / "test.duckdb")
        mock_result = type("R", (), {"returncode": 0, "stdout": "[{]", "stderr": ""})()
        with patch.object(analyze, "DB_PATH", db):
            with patch("subprocess.run", return_value=mock_result):
                result = analyze.query("SELECT 1")
        assert result == []

    def test_invalid_json_returns_empty(
        self, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
    ) -> None:
        db = str(tmp_path / "test.duckdb")
        mock_result = type(
            "R", (), {"returncode": 0, "stdout": "not-json", "stderr": ""}
        )()
        with patch.object(analyze, "DB_PATH", db):
            with patch("subprocess.run", return_value=mock_result):
                result = analyze.query("SELECT 1")
        assert result == []
        assert "WARNING" in capsys.readouterr().err

    def test_valid_json_returned(self, tmp_path: pathlib.Path) -> None:
        db = str(tmp_path / "test.duckdb")
        data = [{"col": "val"}]
        mock_result = type(
            "R", (), {"returncode": 0, "stdout": json.dumps(data), "stderr": ""}
        )()
        with patch.object(analyze, "DB_PATH", db):
            with patch("subprocess.run", return_value=mock_result):
                result = analyze.query("SELECT 1")
        assert result == data


class TestCheckDangerousBash:
    def test_returns_none_when_no_rows(self) -> None:
        with patch.object(analyze, "query", return_value=[]):
            assert analyze.check_dangerous_bash(14, 3) is None

    def test_returns_suggestion_when_rows_found(self) -> None:
        rows = [{"bash_cmd": "rm -rf /tmp/foo", "uses": "5"}]
        with patch.object(analyze, "query", return_value=rows):
            result = analyze.check_dangerous_bash(14, 3)
        assert result is not None
        assert result["id"] == "dangerous-bash"
        assert result["event"] == "PreToolUse"
        assert result["priority"] == "high"


class TestCheckToolMisuse:
    def test_returns_none_when_no_rows(self) -> None:
        with patch.object(analyze, "query", return_value=[]):
            assert analyze.check_tool_misuse(14, 3) is None

    def test_returns_suggestion_with_misuse_types(self) -> None:
        rows = [
            {"misuse_type": "grep/rg → Grep tool", "uses": "100"},
            {"misuse_type": "cat/head/tail → Read tool", "uses": "50"},
        ]
        with patch.object(analyze, "query", return_value=rows):
            result = analyze.check_tool_misuse(14, 3)
        assert result is not None
        assert result["id"] == "tool-misuse"
        assert len(result["examples"]) == 2


class TestCheckHighErrorTools:
    def test_returns_none_when_no_rows(self) -> None:
        with patch.object(analyze, "query", return_value=[]):
            assert analyze.check_high_error_tools(14, 3) is None

    def test_returns_suggestion_with_tool_names(self) -> None:
        rows = [
            {"tool_name": "mcp__github__create_pr", "error_pct": "100.0", "total": "5"}
        ]
        with patch.object(analyze, "query", return_value=rows):
            result = analyze.check_high_error_tools(14, 3)
        assert result is not None
        assert result["id"] == "high-error-tools"
        assert "mcp__github__create_pr" in result["examples"][0]


class TestCheckPermissionFriction:
    def test_returns_none_when_no_rows(self) -> None:
        with patch.object(analyze, "query", return_value=[]):
            assert analyze.check_permission_friction(14, 3) is None

    def test_returns_suggestion_with_denial_examples(self) -> None:
        rows = [
            {"denial": "Permission to use Bash has been denied.", "denials": "10"},
            {"denial": "Permission to use Read has been denied.", "denials": "5"},
        ]
        with patch.object(analyze, "query", return_value=rows):
            result = analyze.check_permission_friction(14, 3)
        assert result is not None
        assert result["id"] == "permission-friction"
        assert result["priority"] == "low"


class TestCheckMissingQualityHooks:
    def _mock_counts(
        self, hook_count: int, stop_count: int
    ) -> Callable[[str], list[dict[str, Any]]]:
        call_count = [0]

        def _q(sql: str) -> list[dict[str, Any]]:  # noqa: ARG001
            call_count[0] += 1
            if call_count[0] == 1:
                return [{"cnt": str(hook_count)}]
            return [{"cnt": str(stop_count)}]

        return _q

    def test_returns_none_when_stop_count_zero(self) -> None:
        with patch.object(analyze, "query", self._mock_counts(0, 0)):
            assert analyze.check_missing_quality_hooks(14) is None

    def test_returns_none_when_ratio_above_threshold(self) -> None:
        with patch.object(analyze, "query", self._mock_counts(60, 100)):
            assert analyze.check_missing_quality_hooks(14) is None

    def test_returns_suggestion_when_low_ratio(self) -> None:
        with patch.object(analyze, "query", self._mock_counts(10, 100)):
            result = analyze.check_missing_quality_hooks(14)
        assert result is not None
        assert result["id"] == "missing-quality-hooks"
        assert result["priority"] == "high"


class TestCheckHookFailures:
    def test_returns_none_when_no_errors(self) -> None:
        with patch.object(analyze, "query", return_value=[]):
            assert analyze.check_hook_failures(14, 3) is None

    def test_returns_suggestion_with_error_examples(self) -> None:
        rows = [{"err": "Timeout after 30s", "cnt": "5"}]
        with patch.object(analyze, "query", return_value=rows):
            result = analyze.check_hook_failures(14, 3)
        assert result is not None
        assert result["id"] == "hook-failures"
        assert "Timeout" in result["examples"][0]


class TestCheckCodeWriteVolume:
    def test_returns_none_when_no_rows(self) -> None:
        with patch.object(analyze, "query", return_value=[]):
            assert analyze.check_code_write_volume(14, 3) is None

    def test_returns_suggestion_with_language_breakdown(self) -> None:
        rows = [
            {"lang": "Python", "writes": "500"},
            {"lang": "Rust", "writes": "200"},
        ]
        with patch.object(analyze, "query", return_value=rows):
            result = analyze.check_code_write_volume(14, 3)
        assert result is not None
        assert result["id"] == "auto-format"
        assert any("Python" in ex for ex in result["examples"])


class TestCheckRepeatedBashPatterns:
    def test_returns_none_when_no_rows(self) -> None:
        with patch.object(analyze, "query", return_value=[]):
            assert analyze.check_repeated_bash_patterns(14, 3) is None

    def test_returns_suggestion_with_commands(self) -> None:
        rows = [
            {"cmd": "git merge origin/main --no-edit", "uses": "50"},
            {"cmd": "git remote get-url origin", "uses": "30"},
        ]
        with patch.object(analyze, "query", return_value=rows):
            result = analyze.check_repeated_bash_patterns(14, 3)
        assert result is not None
        assert result["id"] == "repeated-commands"
        assert any("git merge" in ex for ex in result["examples"])

    def test_long_commands_truncated_in_display(self) -> None:
        long_cmd = "x" * 100
        rows = [{"cmd": long_cmd, "uses": "20"}]
        with patch.object(analyze, "query", return_value=rows):
            result = analyze.check_repeated_bash_patterns(14, 3)
        assert result is not None
        assert len(result["examples"][0].split(" (")[0]) <= 80
