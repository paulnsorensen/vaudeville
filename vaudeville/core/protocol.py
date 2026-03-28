"""Request/response dataclasses and SLM output parser.

Stdlib-only — safe to import in hook scripts.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClassifyRequest:
    rule: str
    input: dict[str, object]

    def to_json_dict(self) -> dict[str, object]:
        return {"rule": self.rule, "input": self.input}


@dataclass
class ClassifyResponse:
    verdict: str   # "violation" | "clean"
    reason: str
    action: str = "block"  # "block" | "warn" | "log"


def parse_verdict(raw: str) -> ClassifyResponse:
    """Parse VERDICT/REASON lines from SLM output.

    Expected format:
        VERDICT: violation
        REASON: one sentence

    Falls back to keyword search if structured format is absent.
    Unknown/malformed output defaults to clean (fail-open).
    """
    verdict = "clean"
    reason = ""

    for line in raw.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("VERDICT:"):
            val = stripped[8:].strip().lower()
            verdict = "violation" if "violation" in val else "clean"
        elif upper.startswith("REASON:"):
            reason = stripped[7:].strip()

    # Fallback: no VERDICT line found — scan for keyword
    has_verdict_line = any(
        line.strip().upper().startswith("VERDICT:") for line in raw.splitlines()
    )
    if not has_verdict_line:
        verdict = "violation" if "violation" in raw.lower() else "clean"
        reason = raw.strip()[:200]

    return ClassifyResponse(verdict=verdict, reason=reason)
