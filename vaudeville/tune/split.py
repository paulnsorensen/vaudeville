"""Deterministic tune/held-out split for eval cases.

Splits test cases 70/30, seeded by rule name + test file mtime.
Small-N escape hatch: if <10 cases, uses the full set for both.
"""

from __future__ import annotations

import hashlib
import logging
import random

from ..eval import EvalCase

logger = logging.getLogger(__name__)

TUNE_RATIO = 0.70
SMALL_N_THRESHOLD = 10


def _compute_seed(rule_name: str, mtime: float) -> int:
    """Derive a deterministic seed from rule name and file mtime."""
    raw = f"{rule_name}:{mtime}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return int(digest, 16)


def split_cases(
    cases: list[EvalCase],
    rule_name: str,
    mtime: float = 0.0,
) -> tuple[list[EvalCase], list[EvalCase]]:
    """Split cases into (tune, held-out) sets.

    Returns (all_cases, all_cases) when len < SMALL_N_THRESHOLD.
    """
    if len(cases) < SMALL_N_THRESHOLD:
        logger.warning(
            "Only %d cases for %s — using full set (no split)",
            len(cases),
            rule_name,
        )
        return list(cases), list(cases)

    seed = _compute_seed(rule_name, mtime)
    indices = list(range(len(cases)))
    rng = random.Random(seed)
    rng.shuffle(indices)

    split_idx = int(len(cases) * TUNE_RATIO)
    tune_indices = sorted(indices[:split_idx])
    held_indices = sorted(indices[split_idx:])

    tune = [cases[i] for i in tune_indices]
    held = [cases[i] for i in held_indices]
    return tune, held
