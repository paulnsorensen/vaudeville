"""One-hop escalation: run a named decide rule once, bounded by a deadline."""

from __future__ import annotations

import threading
from collections.abc import Callable

from vaudeville.server.agents.decide import DecideResult


def escalate(
    decide_fn: Callable[[], DecideResult],
    *,
    deadline: float,
) -> DecideResult | None:
    """Run `decide_fn` once, in a worker thread bounded by `deadline` seconds.

    Returns the result when it completes within the deadline, or None when
    the deadline expires first, so the caller keeps its first decision. The
    caller never nests: this runs the named decide rule exactly once.
    """
    result: list[DecideResult] = []

    def _run() -> None:
        result.append(decide_fn())

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive() or not result:
        return None
    return result[0]
