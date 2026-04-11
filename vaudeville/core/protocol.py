"""Request/response dataclasses and SLM output parser.

Stdlib-only — safe to import in hook scripts.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

_SPECIAL_TOKEN_RE = re.compile(r"<\|[a-z_]+\|>")


@dataclass
class ClassifyRequest:
    prompt: str

    def to_json_dict(self) -> dict[str, object]:
        return {"prompt": self.prompt}


@dataclass
class ClassifyResult:
    """Raw inference output: generated text paired with first-token logprobs."""

    text: str
    logprobs: dict[str, float] = field(default_factory=dict)


@dataclass
class ClassifyResponse:
    verdict: str  # "violation" | "clean"
    reason: str
    confidence: float = 1.0  # P(predicted_class), 0.0–1.0


def parse_verdict(raw: str) -> ClassifyResponse:
    """Parse VERDICT/REASON lines from SLM output.

    Expected format:
        VERDICT: violation
        REASON: one sentence

    Defaults to "clean" (fail-open) if no VERDICT: header is found,
    with a warning log for observability.
    """
    positive_re = re.compile(r"\bviolation\b")

    verdict = "clean"
    reason = ""
    found_verdict = False

    for line in raw.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("VERDICT:"):
            val = stripped[8:].strip().lower()
            verdict = "violation" if positive_re.search(val) else "clean"
            found_verdict = True
        elif upper.startswith("REASON:"):
            reason = stripped[7:].strip()

    if not found_verdict:
        logging.warning("[vaudeville] No VERDICT: header in model output: %.100s", raw)
        verdict = "clean"
        reason = raw.strip()[:200]

    reason = _SPECIAL_TOKEN_RE.sub("", reason).strip()
    return ClassifyResponse(verdict=verdict, reason=reason)


_VIOLATION_PREFIXES = ("violation", "viol", "vi", "v")
_CLEAN_PREFIXES = ("clean", "cl", "c")


def compute_confidence(logprobs: dict[str, float], verdict: str) -> float:
    """Compute confidence from first-token logprobs.

    Finds the best logprob for violation-class and clean-class tokens,
    applies softmax, and returns P(predicted_class). Returns 0.0 (fail-open)
    when logprobs are empty or no matching tokens are found, so the verdict
    won't pass any threshold.
    """
    if not logprobs:
        logging.warning("[vaudeville] Empty logprobs dict — returning 0.0 confidence")
        return 0.0

    best_violation = -math.inf
    best_clean = -math.inf

    for token, lp in logprobs.items():
        normalized = token.strip().lower()
        if any(normalized.startswith(p) for p in _VIOLATION_PREFIXES):
            best_violation = max(best_violation, lp)
        elif any(normalized.startswith(p) for p in _CLEAN_PREFIXES):
            best_clean = max(best_clean, lp)

    if best_violation == -math.inf or best_clean == -math.inf:
        logging.warning(
            "[vaudeville] No violation/clean tokens in logprobs (keys: %s) — returning 0.0 confidence",
            list(logprobs.keys())[:5],
        )
        return 0.0

    # Softmax of two values: P(x) = exp(x) / (exp(x) + exp(y))
    # Use log-sum-exp trick for numerical stability
    max_lp = max(best_violation, best_clean)
    exp_v = math.exp(best_violation - max_lp)
    exp_c = math.exp(best_clean - max_lp)
    total = exp_v + exp_c

    if verdict == "violation":
        return exp_v / total
    return exp_c / total
