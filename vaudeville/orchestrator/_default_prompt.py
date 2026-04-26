"""Default instructions for `vaudeville generate` when none are provided."""

from __future__ import annotations

import os
import subprocess

_IMPACT_FILTER = (
    "Apply the impact filter to every candidate rule before emitting it. A rule is "
    "only worth shipping if its violations can be acted on in a future turn at the "
    "tier you choose:\n"
    "- PreToolUse + block: prevents the action; almost always useful.\n"
    "- Stop + block: forces Claude to keep working until the violation is "
    "fixed; only useful when the violation IS recoverable in continuation "
    "(uncommitted work, dismissed test failure, deferred-to-follow-up-PR).\n"
    "- Stop + shadow/warn: only useful while you're TUNING the rule "
    "(shadow→warn→block ladder). Skip rules whose violation cannot be repaired in "
    "the next turn (already-said sycophantic opener, trailing summary, time the "
    "user already spent) — those are warn-ceiling at best and useless at block, "
    "because forcing a continuation produces a worse outcome than the original "
    "violation.\n"
    "- PostToolUse + shadow/warn after an irreversible write: drop. The file is "
    "already on disk; reframe as a PreToolUse guard or skip.\n\n"
    "Recoverability test: if Claude could plausibly fix the violation in the very "
    "next turn after seeing a block, the rule is shippable. If not, do not emit it."
)

_ANALYTICS_DIRECTIVE = (
    "Mine the patterns below for the top 3 recurring quality regressions in this "
    "user's recent Claude Code sessions and emit one rule per pattern.\n\n"
    f"{_IMPACT_FILTER}\n\n"
    "Patterns:\n\n"
    "{analytics}"
)

_CURATED_BUNDLE = (
    "Generate 3 rules that catch recoverable AI-assistant quality regressions — "
    "violations Claude can fix in the next turn when blocked. Suggested targets:\n"
    "(a) deferral to follow-up PRs / future tickets in PR review replies "
    "(PreToolUse on the comment-posting tool — block prevents the deferral),\n"
    "(b) asking permission to commit/push when the work is clearly done "
    "(Stop — block forces Claude to commit instead of asking),\n"
    "(c) dismissing test/CI failures as 'pre-existing' without fixing or citing "
    "proof (Stop — block forces Claude to fix or substantiate).\n\n"
    "Default tier: shadow (this is the canonical tuning tier — promote via "
    "/tier-advisor once eval data backs it). Include a balanced positive/negative "
    "test set.\n\n"
    f"{_IMPACT_FILTER}"
)


def _run_session_analytics(project_root: str) -> str:
    script = os.path.join(project_root, "commands", "generate", "session-analytics.sh")
    if not os.path.isfile(script):
        return ""
    try:
        result = subprocess.run(
            ["bash", script],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
            env={**os.environ, "VAUDEVILLE_PROJECT_CWD": project_root},
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
        return ""


def build_default_instructions(project_root: str) -> str:
    analytics = _run_session_analytics(project_root)
    if analytics:
        return _ANALYTICS_DIRECTIVE.replace("{analytics}", analytics)
    return _CURATED_BUNDLE
