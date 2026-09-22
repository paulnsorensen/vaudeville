"""Tier ceiling: caps a decided action by the rule's rollout tier (AC-6)."""

from __future__ import annotations

from vaudeville.rules.policy import SILENT_TIER_ALLOWED, WARN_DOWNGRADES

SILENT_TIERS = frozenset({"shadow", "log"})


def apply_tier_ceiling(action_name: str, tier: str) -> tuple[str, str | None]:
    """Return (effective_action_name, downgrade_reason_or_None).

    `disabled` is handled by the caller (the rule is skipped before a
    decide call ever happens), so this function never sees it.
    """
    if tier in SILENT_TIERS:
        if action_name in SILENT_TIER_ALLOWED:
            return action_name, None
        return "allow", f"tier:{tier}"
    if tier == "warn" and action_name in WARN_DOWNGRADES:
        return "warn", "tier:warn"
    return action_name, None
