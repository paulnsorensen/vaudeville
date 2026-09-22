"""F21: every ActionName literal member is classified by the policy tables."""

from __future__ import annotations

from vaudeville.rules.actions import ACTION_NAMES
from vaudeville.rules.policy import PRIMARY_PRECEDENCE, SECONDARY_ACTIONS


def test_every_action_is_classified() -> None:
    classified = set(PRIMARY_PRECEDENCE) | SECONDARY_ACTIONS
    assert classified == set(ACTION_NAMES)


def test_primary_and_secondary_are_disjoint() -> None:
    assert set(PRIMARY_PRECEDENCE).isdisjoint(SECONDARY_ACTIONS)
