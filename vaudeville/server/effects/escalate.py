"""One-hop escalation: run a named decide rule once, bounded by a deadline."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Shared across every escalate call so a burst of concurrent decides caps at
# 8 worker threads instead of spawning one thread per call.
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="vaudeville-decide")


@dataclass(frozen=True)
class EscalateResult(Generic[T]):
    """The outcome of one bounded `decide_fn` run.

    `value` is None on a miss; `timed_out` and `error` distinguish why, so
    callers can log `decide-timeout` vs `decide-error` telemetry.
    """

    value: T | None
    timed_out: bool
    error: BaseException | None = None


def escalate_result(
    decide_fn: Callable[[], T],
    *,
    deadline: float,
    rule_name: str | None = None,
) -> EscalateResult[T]:
    """Run `decide_fn` once on the shared decide pool, bounded by `deadline` seconds.

    On a deadline expiry the future is left to finish in the pool rather
    than cancelled; the pool's fixed size bounds worst-case thread growth.
    An exception raised by `decide_fn` is caught and logged with `rule_name`
    for context rather than propagating. The caller never nests: this runs
    the named decide rule exactly once.
    """
    future: Future[T] = _EXECUTOR.submit(decide_fn)
    try:
        value = future.result(timeout=deadline)
    except FutureTimeoutError:
        return EscalateResult(value=None, timed_out=True, error=None)
    except Exception as exc:
        logger.warning(
            "escalate rule %r: decide_fn raised; keeping the first decision",
            rule_name,
            exc_info=True,
        )
        return EscalateResult(value=None, timed_out=False, error=exc)
    return EscalateResult(value=value, timed_out=False, error=None)


def escalate(
    decide_fn: Callable[[], T],
    *,
    deadline: float,
    rule_name: str | None = None,
) -> T | None:
    """Run `decide_fn` once, bounded by `deadline` seconds.

    Returns the result when it completes within the deadline, or None when
    the deadline expires first or `decide_fn` raises, so the caller keeps
    its first decision. The caller never nests: this runs the named decide
    rule exactly once.
    """
    return escalate_result(decide_fn, deadline=deadline, rule_name=rule_name).value
