"""SLM-based semantic content condensing.

Runs a pre-pass before classification to strip recycled reasoning,
self-quotes, recap sections, and blockquotes that restate earlier content.
The SLM identifies semantic noise that regex cannot catch.

Large texts are chunked at line boundaries so each chunk fits within
the SLM context window (GGUF n_ctx=4096 is the bottleneck).

Fail-open: any error returns the original text unchanged.
"""

from __future__ import annotations

import logging

from ..core import CHARS_PER_TOKEN, sanitize_input
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

# Budget for the single-chunk condensing pass (tokens).
_CONDENSE_MAX_TOKENS = 2000

# Chunking constants — derived from GGUF's 4096 context as the bottleneck.
# Overhead budget: system prompt (~50) + condense template (~75) + chat format (~10) = ~135 tokens.
_CHUNK_INPUT_TOKENS = 1500
_CHUNK_OUTPUT_TOKENS = 1200
_CHUNK_INPUT_CHARS = _CHUNK_INPUT_TOKENS * CHARS_PER_TOKEN
# Cap total chunks to stay under client READ_TIMEOUT (8s).
# ~2.3s per GGUF call * 3 chunks = ~7s.
_MAX_CHUNKS = 3


def _build_condense_prompt(text: str) -> str:
    return CONDENSE_PROMPT.replace("{text}", sanitize_input(text))


def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    lines = text.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > max_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


def _condense_single(
    text: str,
    backend: InferenceBackend,
    max_tokens: int,
) -> str:
    """Returns original text on failure (fail-open)."""
    try:
        prompt = _build_condense_prompt(text)
        result = backend.classify(prompt, max_tokens=max_tokens)
        condensed = result.strip()
        if not condensed:
            logger.warning("Condense returned empty — using original")
            return text
        return condensed
    except Exception as exc:
        logger.warning("Condense failed (%s) — fail open", exc)
        return text


def condense_text(
    text: str,
    backend: InferenceBackend,
) -> str:
    """Run SLM condensing pre-pass on input text.

    Returns condensed text, or the original text on any error (fail-open).
    Short texts (under 200 chars) skip condensing — not enough to recycle.
    Large texts are chunked at line boundaries; chunks beyond _MAX_CHUNKS
    pass through uncondensed.
    """
    if len(text) < 200:
        return text

    if len(text) <= _CHUNK_INPUT_CHARS:
        return _condense_single(text, backend, _CONDENSE_MAX_TOKENS)

    chunks = _split_into_chunks(text, _CHUNK_INPUT_CHARS)
    results: list[str] = []
    for i, chunk in enumerate(chunks):
        if i >= _MAX_CHUNKS:
            results.append(chunk)
            continue
        output_tokens = min(_CHUNK_OUTPUT_TOKENS, len(chunk) // CHARS_PER_TOKEN)
        results.append(_condense_single(chunk, backend, output_tokens))

    return "\n".join(results)
