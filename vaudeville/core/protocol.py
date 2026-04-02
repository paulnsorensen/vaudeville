"""Request/response dataclasses and SLM output parser.

Stdlib-only — safe to import in hook scripts.
"""

from __future__ import annotations

import math
import re
import re
from dataclasses import dataclass, field

_SPECIAL_TOKEN_RE = re.compile(r"<\|[a-z_]+\|>")


@dataclass
class ClassifyRequest:
    rule: str
    input: dict[str, object]

    def to_json_dict(self) -> dict[str, object]:
        return {"rule": self.rule, "input": self.input}


@dataclass
class ClassifyResult:
    """Raw inference output: generated text paired with first-token logprobs."""

    text: str
    logprobs: dict[str, float] = field(default_factory=dict)


@dataclass
class ClassifyResponse:
    verdict: str  # "violation" | "clean"
    reason: str
    action: str = "block"  # "block" | "warn" | "log"
    confidence: float = 1.0  # P(predicted_class), 0.0–1.0


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

    reason = _SPECIAL_TOKEN_RE.sub("", reason).strip()
    return ClassifyResponse(verdict=verdict, reason=reason)


_VIOLATION_PREFIXES = ("violation", "viol", "vi", "v")
_CLEAN_PREFIXES = ("clean", "cl", "c")


def compute_confidence(logprobs: dict[str, float], verdict: str) -> float:
    """Compute confidence from first-token logprobs.

    Finds the best logprob for violation-class and clean-class tokens,
    applies softmax, and returns P(predicted_class). Returns 1.0 (fail-open)
    when logprobs are empty or no matching tokens are found.
    """
    if not logprobs:
        return 1.0

    best_violation = -math.inf
    best_clean = -math.inf

    for token, lp in logprobs.items():
        normalized = token.strip().lower()
        if any(normalized.startswith(p) for p in _VIOLATION_PREFIXES):
            best_violation = max(best_violation, lp)
        elif any(normalized.startswith(p) for p in _CLEAN_PREFIXES):
            best_clean = max(best_clean, lp)

    if best_violation == -math.inf or best_clean == -math.inf:
        return 1.0

    # Softmax of two values: P(x) = exp(x) / (exp(x) + exp(y))
    # Use log-sum-exp trick for numerical stability
    max_lp = max(best_violation, best_clean)
    exp_v = math.exp(best_violation - max_lp)
    exp_c = math.exp(best_clean - max_lp)
    total = exp_v + exp_c

    if verdict == "violation":
        return exp_v / total
    return exp_c / total
