"""Tests for the one-hop escalation helper (AC-10)."""

from __future__ import annotations

import threading
import time

from vaudeville.server.agents.decide import DecideResult
from vaudeville.server.effects import escalate


class TestEscalateRunsOnce:
    def test_runs_named_rule_exactly_once(self) -> None:
        calls: list[int] = []

        def decide_fn() -> DecideResult:
            calls.append(1)
            return DecideResult(outcome="block", reason="bad")

        result = escalate(decide_fn, deadline=1.0)

        assert result == DecideResult(outcome="block", reason="bad")
        assert len(calls) == 1


class TestEscalateDeadlineExpiry:
    def test_first_decision_stands_on_deadline_expiry(self) -> None:
        started = threading.Event()

        def slow_decide_fn() -> DecideResult:
            started.set()
            time.sleep(0.3)
            return DecideResult(outcome="block", reason="too slow")

        first_decision = DecideResult(outcome="allow", reason="first")

        result = escalate(slow_decide_fn, deadline=0.05)

        assert started.wait(1.0)
        assert result is None
        # The caller keeps its first decision when escalate returns None.
        kept = result if result is not None else first_decision
        assert kept == first_decision
