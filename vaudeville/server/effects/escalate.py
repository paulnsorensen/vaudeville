"""One-hop escalation: run a named decide rule once, bounded by a deadline."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def escalate(
    decide_fn: Callable[[], T],
    *,
    deadline: float,
    rule_name: str | None = None,
) -> T | None:
    """Run `decide_fn` once, in a worker thread bounded by `deadline` seconds.

    Returns the result when it completes within the deadline, or None when
    the deadline expires first or `decide_fn` raises, so the caller keeps
    its first decision. An exception is caught and logged with `rule_name`
    for context rather than crashing the escalation thread. The caller
    never nests: this runs the named decide rule exactly once.
    """
    result: list[T] = []

    def _run() -> None:
        try:
            result.append(decide_fn())
        except Exception:
            logger.warning(
                "escalate rule %r: decide_fn raised; keeping the first decision",
                rule_name,
                exc_info=True,
            )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive() or not result:
        return None
    return result[0]
