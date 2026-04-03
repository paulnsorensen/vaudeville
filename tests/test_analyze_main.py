"""Tests for analyze.main()."""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
from unittest.mock import patch

import pytest

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


class TestMain:
    def test_exits_when_no_db(self, tmp_path: pathlib.Path) -> None:
        nonexistent = str(tmp_path / "no.duckdb")
        with patch.object(analyze, "DB_PATH", nonexistent):
            with pytest.raises(SystemExit) as exc_info:
                analyze.main()
            assert exc_info.value.code == 1

    def test_json_output_is_valid_list(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_db = str(tmp_path / "sessions.duckdb")
        open(fake_db, "w").close()
        with (
            patch.object(analyze, "DB_PATH", fake_db),
            patch.object(analyze, "query", return_value=[]),
            patch.object(sys, "argv", ["analyze.py", "--json"]),
        ):
            analyze.main()
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)

    def test_text_output_shows_no_suggestions_message(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_db = str(tmp_path / "sessions.duckdb")
        open(fake_db, "w").close()
        with (
            patch.object(analyze, "DB_PATH", fake_db),
            patch.object(analyze, "query", return_value=[]),
            patch.object(sys, "argv", ["analyze.py"]),
        ):
            analyze.main()
        assert "No hook suggestions" in capsys.readouterr().out

    def test_days_flag_respected(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_db = str(tmp_path / "sessions.duckdb")
        open(fake_db, "w").close()
        rows = [{"bash_cmd": "rm -rf /tmp/x", "uses": "5"}]
        with (
            patch.object(analyze, "DB_PATH", fake_db),
            patch.object(analyze, "query", return_value=rows),
            patch.object(sys, "argv", ["analyze.py", "--days", "7"]),
        ):
            analyze.main()
        out = capsys.readouterr().out
        assert "7 days" in out

    def test_suggestions_printed_in_text_mode(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_db = str(tmp_path / "sessions.duckdb")
        open(fake_db, "w").close()
        rows = [{"bash_cmd": "rm -rf /tmp/x", "uses": "5"}]
        with (
            patch.object(analyze, "DB_PATH", fake_db),
            patch.object(analyze, "query", return_value=rows),
            patch.object(sys, "argv", ["analyze.py"]),
        ):
            analyze.main()
        out = capsys.readouterr().out
        assert "HOOK SUGGESTIONS" in out

    def test_analyzer_exception_is_caught(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_db = str(tmp_path / "sessions.duckdb")
        open(fake_db, "w").close()

        def _boom(_sql: str) -> list[object]:
            raise RuntimeError("db exploded")

        with (
            patch.object(analyze, "DB_PATH", fake_db),
            patch.object(analyze, "query", side_effect=_boom),
            patch.object(sys, "argv", ["analyze.py"]),
        ):
            analyze.main()
        err = capsys.readouterr().err
        assert "WARNING" in err
