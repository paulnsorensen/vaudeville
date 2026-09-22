"""F21: every ActionName literal member is classified by the policy tables."""

from __future__ import annotations

from typing import get_args

from vaudeville.rules.actions import ActionName
from vaudeville.rules.policy import PRIMARY_PRECEDENCE, SECONDARY_ACTIONS


def test_every_action_is_classified() -> None:
    classified = set(PRIMARY_PRECEDENCE) | SECONDARY_ACTIONS
    assert classified == set(get_args(ActionName))


def test_primary_and_secondary_are_disjoint() -> None:
    assert set(PRIMARY_PRECEDENCE).isdisjoint(SECONDARY_ACTIONS)
