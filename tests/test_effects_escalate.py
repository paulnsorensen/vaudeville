"""Tests for the one-hop escalation helper (AC-10)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import pytest

from vaudeville.server.effects import escalate


@dataclass(frozen=True)
class FakeResult:
    outcome: str
    reason: str


class TestEscalateRunsOnce:
    def test_runs_named_rule_exactly_once(self) -> None:
        calls: list[int] = []

        def decide_fn() -> FakeResult:
            calls.append(1)
            return FakeResult(outcome="block", reason="bad")

        result = escalate(decide_fn, deadline=1.0)

        assert result == FakeResult(outcome="block", reason="bad")
        assert len(calls) == 1


class TestEscalateDeadlineExpiry:
    def test_first_decision_stands_on_deadline_expiry(self) -> None:
        started = threading.Event()

        def slow_decide_fn() -> FakeResult:
            started.set()
            time.sleep(0.3)
            return FakeResult(outcome="block", reason="too slow")

        first_decision = FakeResult(outcome="allow", reason="first")

        result = escalate(slow_decide_fn, deadline=0.05)

        assert started.wait(1.0)
        assert result is None
        # The caller keeps its first decision when escalate returns None.
        kept = result if result is not None else first_decision
        assert kept == first_decision


class TestEscalateHandlesException:
    def test_raising_decide_fn_returns_none_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def raising_decide_fn() -> FakeResult:
            raise RuntimeError("boom")

        with caplog.at_level(
            logging.WARNING, logger="vaudeville.server.effects.escalate"
        ):
            result = escalate(
                raising_decide_fn, deadline=1.0, rule_name="escalate-rule"
            )

        assert result is None
        assert any("escalate-rule" in record.getMessage() for record in caplog.records)
