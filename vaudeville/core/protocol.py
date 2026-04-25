"""Request/response dataclasses and SLM output parser.

Stdlib-only — safe to import in hook scripts.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

_SPECIAL_TOKEN_RE = re.compile(r"<\|[a-z_]+\|>")
_FIRST_SENTENCE_RE = re.compile(r"^(.*?[.!?])(?:\s|$|(?=[A-Z]))")
_NEGATION_VIOLATION_RE = re.compile(
    r"\b(?:no\s+violation|not\s+(?:a\s+)?violation|isn't\s+(?:a\s+)?violation|is\s+not\s+(?:a\s+)?violation)\b[.!?,;:)]*"
)
_VIOLATION_RE = re.compile(r"\bviolation\b")
# Budget for a classify verdict: `VERDICT: <label>\nREASON: <one short sentence>`.
# ~5 tokens for VERDICT line + ~20 for REASON gives a tight cap that prevents
# Phi-4-mini from hallucinating extra sentences past the REASON line.
CLASSIFY_MAX_TOKENS = 30


@dataclass
class ClassifyRequest:
    prompt: str
    rule: str = ""

    prefix_len: int = 0  # 0 = no caching (backward compatible)
    tier: str = "enforce"
    input_text: str = ""  # raw LLM output text, before prompt construction

    def to_json_dict(self) -> dict[str, object]:
        d: dict[str, object] = {"prompt": self.prompt}
        if self.rule:
            d["rule"] = self.rule
        if self.prefix_len > 0:
            d["prefix_len"] = self.prefix_len
        if self.tier != "enforce":
            d["tier"] = self.tier
        if self.input_text:
            d["input_text"] = self.input_text
        return d


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
    verdict = "clean"
    reason = ""
    found_verdict = False

    for line in raw.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("VERDICT:"):
            val = stripped[8:].strip().lower()
            if _NEGATION_VIOLATION_RE.search(val):
                verdict = "clean"
            elif _VIOLATION_RE.search(val):
                verdict = "violation"
            else:
                verdict = "clean"
            found_verdict = True
        elif upper.startswith("REASON:"):
            reason = stripped[7:].strip()

    if not found_verdict:
        logging.warning("[vaudeville] No VERDICT: header in model output: %.100s", raw)
        verdict = "clean"
        reason = raw.strip()[:200]

    reason = _SPECIAL_TOKEN_RE.sub("", reason).strip()
    m = _FIRST_SENTENCE_RE.match(reason)
    if m:
        reason = m.group(1)
    return ClassifyResponse(verdict=verdict, reason=reason)


def compute_confidence(logprobs: dict[str, float], verdict: str) -> float:
    """Compute confidence from first-token logprobs.

    Matches the exact tokens "violation" and "clean" (after stripping
    whitespace, SentencePiece prefix, and lowercasing). Returns 0.0
    (fail-open) when logprobs are empty or no matching tokens are found.
    """
    if not logprobs:
        logging.warning("[vaudeville] Empty logprobs dict — returning 0.0 confidence")
        return 0.0

    best_violation = -math.inf
    best_clean = -math.inf

    for token, lp in logprobs.items():
        normalized = token.strip().lstrip("▁").lower()
        if normalized == "violation":
            best_violation = max(best_violation, lp)
        elif normalized == "clean":
            best_clean = max(best_clean, lp)

    if best_violation == -math.inf and best_clean == -math.inf:
        logging.warning(
            "[vaudeville] No violation/clean tokens in logprobs (keys: %s)"
            " — returning 0.0 confidence",
            list(logprobs.keys())[:5],
        )
        return 0.0

    # One class missing from top-K means the model is highly confident
    # in the other class — use 0.95 for the dominant class.
    if best_violation == -math.inf:
        return 0.95 if verdict == "clean" else 0.05
    if best_clean == -math.inf:
        return 0.95 if verdict == "violation" else 0.05

    # Softmax of two values: P(x) = exp(x) / (exp(x) + exp(y))
    # Use log-sum-exp trick for numerical stability
    max_lp = max(best_violation, best_clean)
    exp_v = math.exp(best_violation - max_lp)
    exp_c = math.exp(best_clean - max_lp)
    total = exp_v + exp_c

    if verdict == "violation":
        return exp_v / total
    return exp_c / total
