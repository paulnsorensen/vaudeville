"""SLM-based semantic content condensing.

Runs a pre-pass before classification to strip recycled reasoning,
self-quotes, recap sections, and blockquotes that restate earlier content.
The SLM identifies semantic noise that regex cannot catch.

Fail-open: any error returns the original text unchanged.
"""

from __future__ import annotations

import logging

from .inference import InferenceBackend

logger = logging.getLogger(__name__)

CONDENSE_PROMPT = """\
Remove recycled content from the text below. Keep only NEW reasoning.

Strip:
- Blockquotes restating earlier content
- "As I mentioned" / "As noted above" recap phrases and their sentences
- Self-quotes or repeated conclusions
- Summaries of what was already said

Keep everything else exactly as-is. Return ONLY the condensed text.

TEXT:
{text}

CONDENSED:
"""

# Budget for the condensing pass (tokens). The SLM output should be
# shorter than the input — cap generously to avoid runaway generation.
_CONDENSE_MAX_TOKENS = 2000


def _build_condense_prompt(text: str) -> str:
    """Format the condensing prompt with input text."""
    return CONDENSE_PROMPT.replace("{text}", text)


def condense_text(
    text: str,
    backend: InferenceBackend,
) -> str:
    """Run SLM condensing pre-pass on input text.

    Returns condensed text, or the original text on any error (fail-open).
    Short texts (under 200 chars) skip condensing — not enough to recycle.
    """
    if len(text) < 200:
        return text
    try:
        prompt = _build_condense_prompt(text)
        result = backend.classify(prompt, max_tokens=_CONDENSE_MAX_TOKENS)
        condensed = result.strip()
        if not condensed:
            logger.warning("Condense returned empty — using original")
            return text
        return condensed
    except Exception as exc:
        logger.warning("Condense failed (%s) — fail open", exc)
        return text
