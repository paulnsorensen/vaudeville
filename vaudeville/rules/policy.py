"""Tier ceiling and precedence policy tables, keyed by the action vocabulary.

Lives beside `Action` so the ten-action vocabulary and its policy tables stay
in one place; `vaudeville.server.hook.tier` and `.precedence` consume these.
"""

from __future__ import annotations

from .actions import ACTION_NAMES, ActionName

# Under `warn`, these primary-channel actions downgrade to `warn`.
WARN_DOWNGRADES: frozenset[ActionName] = frozenset(
    {"block", "ask", "rewrite", "feedback"}
)

# Under `shadow`/`log`, every action except these two becomes a no-op.
SILENT_TIER_ALLOWED: frozenset[ActionName] = frozenset({"allow", "log"})

# Most restrictive first; the primary channel of a precedence merge goes to
# whichever matching rule's action ranks earliest here.
PRIMARY_PRECEDENCE: tuple[ActionName, ...] = (
    "block",
    "ask",
    "rewrite",
    "feedback",
    "warn",
)

# Every action outside the primary channel: applies in addition to (or
# instead of, when there is no primary) whichever primary action wins.
SECONDARY_ACTIONS: frozenset[ActionName] = frozenset(
    {"add-context", "log", "run", "allow", "escalate"}
)

_unclassified = (
    frozenset(ACTION_NAMES) - frozenset(PRIMARY_PRECEDENCE) - SECONDARY_ACTIONS
)
assert not _unclassified, (
    f"ActionName members missing from policy tables: {_unclassified}"
)
