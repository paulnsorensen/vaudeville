"""Logprob extraction utilities for MLX inference."""

from __future__ import annotations

import logging
from typing import Any, cast

TOP_K_LOGPROBS = 10

logger = logging.getLogger(__name__)


def extract_top_logprobs(logprobs_arr: Any, tokenizer: Any) -> dict[str, float]:
    """Extract top-K token logprobs from the full vocab distribution."""
    import mlx.core as mx

    try:
        mx.eval(logprobs_arr)
        top_indices = mx.argpartition(logprobs_arr, kth=-TOP_K_LOGPROBS)[
            -TOP_K_LOGPROBS:
        ]
        mx.eval(top_indices)

        result: dict[str, float] = {}
        for idx in cast(list[Any], top_indices.tolist()):
            token_str: str = tokenizer.decode([idx])
            result[token_str] = logprobs_arr[idx].item()
        return result
    except Exception as exc:
        logger.warning("[vaudeville] Logprob extraction failed: %s", exc)
        return {}
