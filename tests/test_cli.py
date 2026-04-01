"""Tests for the vaudeville CLI entry point."""

from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest


class TestFindLogFiles:
    def test_returns_logs_with_live_daemon(self, tmp_path: Path) -> None:
        log = tmp_path / "vaudeville-abc123.log"
        pid = tmp_path / "vaudeville-abc123.pid"
        log.write_text("some log")
        pid.write_text(str(os.getpid()))  # current process is alive

        with patch("vaudeville.__main__.glob.glob", return_value=[str(log)]):
            from vaudeville.__main__ import _find_log_files

            # Patch the pid path derivation to use tmp_path
            with patch(
                "vaudeville.__main__.Path.read_text",
                return_value=str(os.getpid()),
            ):
                result = _find_log_files()

        assert result == [str(log)]

    def test_skips_logs_with_dead_daemon(self, tmp_path: Path) -> None:
        log = tmp_path / "vaudeville-dead.log"
        log.write_text("some log")

        with patch("vaudeville.__main__.glob.glob", return_value=[str(log)]):
            from vaudeville.__main__ import _find_log_files

            # PID file missing → FileNotFoundError → skip
            result = _find_log_files()

        assert result == []

    def test_skips_stale_pid(self, tmp_path: Path) -> None:
        log = tmp_path / "vaudeville-stale.log"
        log.write_text("some log")

        with patch("vaudeville.__main__.glob.glob", return_value=[str(log)]):
            with patch(
                "vaudeville.__main__.Path.read_text",
                return_value="99999999",
            ):
                with patch(
                    "vaudeville.__main__.os.kill",
                    side_effect=ProcessLookupError,
                ):
                    from vaudeville.__main__ import _find_log_files

                    result = _find_log_files()

        assert result == []


class TestCmdTail:
    def _make_args(
        self, session: str | None = None, all: bool = False
    ) -> Namespace:
        return Namespace(session=session, all=all)

    def test_no_active_daemons_exits(self) -> None:
        with patch("vaudeville.__main__._find_log_files", return_value=[]):
            from vaudeville.__main__ import cmd_tail

            with pytest.raises(SystemExit, match="1"):
                cmd_tail(self._make_args())

    def test_single_session_tails(self) -> None:
        with patch(
            "vaudeville.__main__._find_log_files",
            return_value=["/tmp/vaudeville-abc.log"],
        ):
            with patch("vaudeville.__main__.subprocess.run") as mock_run:
                from vaudeville.__main__ import cmd_tail

                cmd_tail(self._make_args())

        mock_run.assert_called_once_with(
            ["tail", "-f", "/tmp/vaudeville-abc.log"]
        )

    def test_multiple_sessions_without_all_exits(self) -> None:
        logs = ["/tmp/vaudeville-a.log", "/tmp/vaudeville-b.log"]
        with patch("vaudeville.__main__._find_log_files", return_value=logs):
            from vaudeville.__main__ import cmd_tail

            with pytest.raises(SystemExit, match="1"):
                cmd_tail(self._make_args())

    def test_multiple_sessions_with_all_tails_all(self) -> None:
        logs = ["/tmp/vaudeville-a.log", "/tmp/vaudeville-b.log"]
        with patch("vaudeville.__main__._find_log_files", return_value=logs):
            with patch("vaudeville.__main__.subprocess.run") as mock_run:
                from vaudeville.__main__ import cmd_tail

                cmd_tail(self._make_args(all=True))

        mock_run.assert_called_once_with(["tail", "-f"] + logs)

    def test_session_flag_overrides_discovery(self) -> None:
        logs = ["/tmp/vaudeville-a.log", "/tmp/vaudeville-b.log"]
        with patch("vaudeville.__main__._find_log_files", return_value=logs):
            with patch("vaudeville.__main__.os.path.exists", return_value=True):
                with patch(
                    "vaudeville.__main__.subprocess.run"
                ) as mock_run:
                    from vaudeville.__main__ import cmd_tail

                    cmd_tail(self._make_args(session="b"))

        mock_run.assert_called_once_with(
            ["tail", "-f", "/tmp/vaudeville-b.log"]
        )

    def test_session_flag_missing_log_exits(self) -> None:
        with patch(
            "vaudeville.__main__._find_log_files",
            return_value=["/tmp/vaudeville-a.log"],
        ):
            with patch("vaudeville.__main__.os.path.exists", return_value=False):
                from vaudeville.__main__ import cmd_tail

                with pytest.raises(SystemExit, match="1"):
                    cmd_tail(self._make_args(session="missing"))
