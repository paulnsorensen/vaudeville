"""Request/response dataclasses and SLM output parser.

Stdlib-only — safe to import in hook scripts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ClassifyRequest:
    rule: str
    input: dict[str, object]

    def to_json_dict(self) -> dict[str, object]:
        return {"rule": self.rule, "input": self.input}


@dataclass
class ClassifyResponse:
    verdict: str  # "violation" | "clean"
    reason: str
    action: str = "block"  # "block" | "warn" | "log"


def parse_verdict(
    raw: str,
    labels: list[str] | None = None,
) -> ClassifyResponse:
    """Parse VERDICT/REASON lines from SLM output.

    Expected format:
        VERDICT: violation
        REASON: one sentence

    ``labels`` is [positive, negative] — the positive label triggers
    the rule's action. Falls back to keyword search if structured format
    is absent. Unknown/malformed output defaults to negative (fail-open).
    """
    # Duplicated default — must match DEFAULT_LABELS in rules.py.
    # Cannot import from rules.py (it pulls PyYAML; protocol.py is stdlib-only).
    if labels is None:
        labels = ["violation", "clean"]
    positive_re = re.compile(r"\b" + re.escape(labels[0].lower()) + r"\b")

    verdict = labels[1]
    reason = ""

    for line in raw.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("VERDICT:"):
            val = stripped[8:].strip().lower()
            verdict = labels[0] if positive_re.search(val) else labels[1]
        elif upper.startswith("REASON:"):
            reason = stripped[7:].strip()

    # Fallback: no VERDICT line found — scan for keyword
    has_verdict_line = any(
        line.strip().upper().startswith("VERDICT:") for line in raw.splitlines()
    )
    if not has_verdict_line:
        verdict = labels[0] if positive_re.search(raw.lower()) else labels[1]
        reason = raw.strip()[:200]

    return ClassifyResponse(verdict=verdict, reason=reason)
