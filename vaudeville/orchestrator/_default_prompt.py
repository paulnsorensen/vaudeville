"""Default instructions for `vaudeville generate` when none are provided."""

from __future__ import annotations

import os
import subprocess

_ANALYTICS_DIRECTIVE = (
    "Mine the patterns below for the top 3 recurring quality regressions in this "
    "user's recent Claude Code sessions and emit one rule per pattern. Patterns:\n\n"
    "{analytics}"
)

_CURATED_BUNDLE = (
    "Generate 3 rules that catch common AI-assistant quality regressions: "
    "(a) hedging ('should work', 'might want to'), "
    "(b) premature completion (claiming done with TODOs), "
    "(c) sycophancy / test-failure dismissal ('pre-existing issue', 'great question!'). "
    "Use the Stop event. Tier shadow. Include a balanced positive/negative test set."
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
