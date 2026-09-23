"""Tests for the one-hop escalation helper (AC-10)."""

from __future__ import annotations

import importlib
import logging
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass

import pytest

from vaudeville.server.effects import escalate, escalate_result

escalate_module = importlib.import_module("vaudeville.server.effects.escalate")


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


class TestEscalateResult:
    def test_success_carries_the_value_with_no_timeout_or_error(self) -> None:
        outcome = escalate_result(
            lambda: FakeResult(outcome="clean", reason=""), deadline=1.0
        )

        assert outcome.value == FakeResult(outcome="clean", reason="")
        assert outcome.timed_out is False
        assert outcome.error is None

    def test_deadline_expiry_sets_timed_out_true_and_no_error(self) -> None:
        started = threading.Event()

        def slow_decide_fn() -> FakeResult:
            started.set()
            time.sleep(0.3)
            return FakeResult(outcome="block", reason="too slow")

        outcome = escalate_result(slow_decide_fn, deadline=0.05)

        assert started.wait(1.0)
        assert outcome.value is None
        assert outcome.timed_out is True
        assert outcome.error is None

    def test_raising_decide_fn_carries_the_exception_and_no_timeout(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def raising_decide_fn() -> FakeResult:
            raise RuntimeError("boom")

        with caplog.at_level(
            logging.WARNING, logger="vaudeville.server.effects.escalate"
        ):
            outcome = escalate_result(
                raising_decide_fn, deadline=1.0, rule_name="escalate-rule"
            )

        assert outcome.value is None
        assert outcome.timed_out is False
        assert isinstance(outcome.error, RuntimeError)

    @pytest.mark.parametrize("deadline", [0.0, -1.0])
    def test_spent_budget_times_out_without_calling_decide_fn(
        self, deadline: float
    ) -> None:
        """F8: no model call starts once the caller's budget is gone."""
        calls: list[int] = []

        def decide_fn() -> FakeResult:
            calls.append(1)
            return FakeResult(outcome="block", reason="late")

        outcome = escalate_result(decide_fn, deadline=deadline)

        assert outcome.value is None
        assert outcome.timed_out is True
        assert calls == []

    def test_deadline_expiry_cancels_the_future(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F8: a queued call that missed its deadline must not run later."""
        cancelled: list[bool] = []

        class _StalledFuture:
            def result(self, timeout: float) -> FakeResult:
                raise FutureTimeoutError

            def cancel(self) -> bool:
                cancelled.append(True)
                return True

        class _Executor:
            def submit(self, fn: object) -> _StalledFuture:
                return _StalledFuture()

        monkeypatch.setattr(escalate_module, "_EXECUTOR", _Executor())

        outcome = escalate_result(lambda: FakeResult("block", "x"), deadline=0.05)

        assert outcome.timed_out is True
        assert cancelled == [True]


class TestEscalatePool:
    def test_concurrent_timeouts_stay_bounded_by_the_pool_size(self) -> None:
        """20 timed-out calls run on the shared 8-worker pool, not one thread
        each, so live thread growth above the pre-test baseline stays <= 8."""
        baseline = threading.active_count()

        def slow_decide_fn() -> FakeResult:
            time.sleep(0.3)
            return FakeResult(outcome="block", reason="slow")

        for _ in range(20):
            result = escalate(slow_decide_fn, deadline=0.01)
            assert result is None

        assert threading.active_count() - baseline <= 8
