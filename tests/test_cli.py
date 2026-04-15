"""Tests for the vaudeville CLI entry point."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestCmdWatch:
    def test_watch_calls_watch_function(self) -> None:
        from argparse import Namespace

        with patch("vaudeville.server.watch") as mock_watch:
            from vaudeville.__main__ import cmd_watch

            cmd_watch(Namespace(log_path="/tmp/test.jsonl"))

        mock_watch.assert_called_once_with(log_path="/tmp/test.jsonl")


class TestCmdStats:
    def test_stats_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        from argparse import Namespace

        mock_result = {
            "total": 1,
            "time_range": {"earliest": "t0", "latest": "t1"},
            "rules": {},
            "latency": {
                "p50_ms": 1.0,
                "p95_ms": 2.0,
                "mean_ms": 1.5,
                "histogram": {},
            },
        }
        with patch("vaudeville.server.aggregate_events", return_value=mock_result):
            from vaudeville.__main__ import cmd_stats

            cmd_stats(Namespace(log_path="/tmp/test.jsonl", json=True))

        out = capsys.readouterr().out
        assert '"total": 1' in out

    def test_stats_no_events(self, capsys: pytest.CaptureFixture[str]) -> None:
        from argparse import Namespace

        mock_result = {"total": 0}
        with patch("vaudeville.server.aggregate_events", return_value=mock_result):
            from vaudeville.__main__ import cmd_stats

            cmd_stats(Namespace(log_path="/tmp/test.jsonl", json=False))

        out = capsys.readouterr().out
        assert "No events recorded" in out


class TestCmdSetup:
    def test_setup_calls_setup_main(self) -> None:
        from argparse import Namespace

        with patch("vaudeville.setup.main") as mock_setup:
            from vaudeville.__main__ import cmd_setup

            cmd_setup(Namespace())

        mock_setup.assert_called_once_with()


class TestMain:
    def test_no_command_prints_help_and_exits(self) -> None:
        with patch("sys.argv", ["vaudeville"]):
            from vaudeville.__main__ import main

            with pytest.raises(SystemExit, match="1"):
                main()

    def test_setup_command_dispatches(self) -> None:
        with (
            patch("sys.argv", ["vaudeville", "setup"]),
            patch("vaudeville.setup.main") as mock_setup,
        ):
            from vaudeville.__main__ import main

            main()

        mock_setup.assert_called_once_with()
