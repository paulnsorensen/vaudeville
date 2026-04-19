"""Text truncation strategies for vaudeville rule prompts.

Event-aware truncation: Stop -> sandwich, PreToolUse -> front, default -> back.
Code block stripping reduces token waste on structural noise.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


MAX_INPUT_TOKENS = 3000
CHARS_PER_TOKEN = 4


def back_truncate(text: str, max_tokens: int = MAX_INPUT_TOKENS) -> str:
    """Keep the last max_tokens tokens (approx). Violations cluster at the end."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def front_truncate(text: str, max_tokens: int = MAX_INPUT_TOKENS) -> str:
    """Keep the first max_tokens tokens (approx). For context at turn start."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def sandwich_truncate(text: str, max_tokens: int = MAX_INPUT_TOKENS) -> str:
    """Keep head + tail slices. Violations cluster at end but beginning gives context."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    marker = "\n[...]\n"
    available = max_chars - len(marker)
    if available <= 0:
        return back_truncate(text, max_tokens)
    head_chars = available * 3 // 10
    tail_chars = available - head_chars
    return text[:head_chars] + marker + text[-tail_chars:]


def _truncate_for_event(
    text: str,
    event: str,
    max_tokens: int = MAX_INPUT_TOKENS,
) -> str:
    if event == "Stop":
        return sandwich_truncate(text, max_tokens)
    if event == "PreToolUse":
        return front_truncate(text, max_tokens)
    return back_truncate(text, max_tokens)


_CODE_BLOCK_RE = re.compile(
    r"^```[^\n]*\n.*?^```\s*$",
    re.MULTILINE | re.DOTALL,
)


def _strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks -- they consume tokens but rarely contain violations."""
    return _CODE_BLOCK_RE.sub("", text)


def prepare_text(text: str, event: str) -> str:
    """Strip structural noise before truncation.

    Only applies to Stop hooks (assistant response quality).
    Other event types pass through unmodified.
    Fail-open: returns original text on any error.
    """
    if event != "Stop":
        return text
    try:
        return _strip_code_blocks(text)
    except Exception as exc:
        logger.warning("strip_code_blocks failed (%s) — fail open", exc)
        return text
