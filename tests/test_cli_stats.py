"""Tests for the ``vaudeville stats`` and ``vaudeville watch`` CLI subcommands."""

from __future__ import annotations

import json
from argparse import Namespace
from typing import Any
from unittest.mock import patch

import pytest

from vaudeville.__main__ import _print_stats_human, cmd_stats, cmd_watch, main
from vaudeville.server import empty_result


def _sample_result() -> dict[str, Any]:
    """A minimal aggregated result for testing."""
    return {
        "total": 5,
        "rules": {
            "no-yolo": {
                "total": 3,
                "violations": 1,
                "pass_rate": 66.7,
                "avg_latency_ms": 120.5,
            },
            "no-todo": {
                "total": 2,
                "violations": 0,
                "pass_rate": 100.0,
                "avg_latency_ms": 80.0,
            },
        },
        "latency": {
            "p50_ms": 100.0,
            "p95_ms": 200.0,
            "mean_ms": 110.0,
            "histogram": {
                "<=50ms": 0,
                "<=100ms": 2,
                "<=200ms": 2,
                "<=500ms": 1,
                "<=1000ms": 0,
                ">1000ms": 0,
            },
        },
        "time_range": {
            "earliest": "2026-04-12T10:00:00+00:00",
            "latest": "2026-04-12T11:00:00+00:00",
        },
    }


class TestCmdStats:
    def test_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = Namespace(json=True, log_path="/fake/events.jsonl")
        with patch(
            "vaudeville.server.aggregate_events",
            return_value=_sample_result(),
        ):
            cmd_stats(args)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["total"] == 5
        assert "no-yolo" in parsed["rules"]

    def test_human_output_contains_rule_table(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = Namespace(json=False, log_path="/fake/events.jsonl")
        with patch(
            "vaudeville.server.aggregate_events",
            return_value=_sample_result(),
        ):
            cmd_stats(args)
        captured = capsys.readouterr()
        assert "no-yolo" in captured.out
        assert "no-todo" in captured.out
        assert "Total classifications: 5" in captured.out

    def test_human_output_empty_log(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = Namespace(json=False, log_path="/fake/events.jsonl")
        with patch(
            "vaudeville.server.aggregate_events",
            return_value=empty_result(),
        ):
            cmd_stats(args)
        captured = capsys.readouterr()
        assert "No events recorded yet." in captured.out


class TestPrintStatsHuman:
    def test_empty_result(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_stats_human(empty_result())
        captured = capsys.readouterr()
        assert "No events recorded yet." in captured.out

    def test_includes_latency_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_stats_human(_sample_result())
        captured = capsys.readouterr()
        assert "p50=100.0ms" in captured.out
        assert "p95=200.0ms" in captured.out

    def test_includes_histogram(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_stats_human(_sample_result())
        captured = capsys.readouterr()
        assert "Histogram:" in captured.out
        assert "<=100ms" in captured.out

    def test_includes_time_range(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_stats_human(_sample_result())
        captured = capsys.readouterr()
        assert "2026-04-12T10:00:00" in captured.out

    def test_pass_rate_formatting(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_stats_human(_sample_result())
        captured = capsys.readouterr()
        assert "66.7%" in captured.out
        assert "100.0%" in captured.out


class TestCmdWatch:
    def test_calls_watch_with_log_path(self) -> None:
        args = Namespace(log_path="/fake/events.jsonl")
        with patch("vaudeville.server.watch.watch") as mock_watch:
            cmd_watch(args)
        mock_watch.assert_called_once_with(log_path="/fake/events.jsonl")

    def test_catches_keyboard_interrupt(self) -> None:
        args = Namespace(log_path="/fake/events.jsonl")
        with patch("vaudeville.server.watch.watch", side_effect=KeyboardInterrupt):
            cmd_watch(args)  # should not raise

    def test_main_dispatches_watch(self) -> None:
        with patch("sys.argv", ["vaudeville", "watch"]):
            with patch("vaudeville.server.watch.watch") as mock_watch:
                mock_watch.side_effect = KeyboardInterrupt
                main()
                mock_watch.assert_called_once()

    def test_main_watch_custom_log_path(self) -> None:
        with patch("sys.argv", ["vaudeville", "watch", "--log-path", "/custom.jsonl"]):
            with patch("vaudeville.server.watch.watch") as mock_watch:
                main()
                mock_watch.assert_called_once_with(log_path="/custom.jsonl")
