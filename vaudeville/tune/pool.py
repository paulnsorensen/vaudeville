"""Candidate pool I/O and LLM-driven authoring.

Generates new prompt examples targeting false-negative clusters,
writes them to a candidates YAML file, and injects them into the
rule's search space for Optuna trials.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from collections.abc import Sequence
from typing import Any

import yaml

from ..core.rules import Example, Rule
from ..eval import CaseResult

logger = logging.getLogger(__name__)

STALL_THRESHOLD = 3
AUTHOR_INTERVAL = 5
MAX_NEW_CANDIDATES = 5
AUTHOR_MODEL = "claude-sonnet-4-20250514"


def should_author(trial_number: int, stall_count: int) -> bool:
    """Check if authoring should fire this trial."""
    if stall_count >= STALL_THRESHOLD:
        return True
    return trial_number > 0 and trial_number % AUTHOR_INTERVAL == 0


def collect_fn_texts(case_results: list[CaseResult]) -> list[str]:
    """Extract false-negative texts from case results."""
    return [
        cr.text
        for cr in case_results
        if cr.label == "violation" and cr.predicted == "clean"
    ]


def compute_stall_count(
    trial_values: Sequence[Sequence[float]],
) -> int:
    """Count consecutive non-improving trials from the end.

    Each entry is (recall_held, precision_held).
    """
    if len(trial_values) < 2:
        return 0

    best_recall = 0.0
    stall = 0
    for vals in trial_values:
        recall = vals[0]
        if recall > best_recall:
            best_recall = recall
            stall = 0
        else:
            stall += 1
    return stall


def _next_candidate_id(existing_ids: list[str]) -> int:
    """Find the next numeric suffix for authored candidates."""
    max_n = 0
    for eid in existing_ids:
        if eid.startswith("authored-"):
            try:
                n = int(eid.split("-", 1)[1])
                max_n = max(max_n, n)
            except ValueError:
                continue
    return max_n + 1


def _build_author_prompt(
    rule_name: str,
    fn_texts: list[str],
) -> str:
    """Build the LLM prompt for candidate generation."""
    fn_block = "\n---\n".join(fn_texts[:10])
    return (
        f"You are authoring test examples for a text classifier rule "
        f"named '{rule_name}'.\n\n"
        f"The following texts were FALSE NEGATIVES — the classifier "
        f"missed them as violations:\n\n{fn_block}\n\n"
        f"Generate 3-5 new example inputs that capture similar "
        f"violation patterns. Each should be distinct and concise "
        f"(under 200 chars).\n\n"
        f"Respond with EXACTLY this JSON format:\n"
        f'{{"candidates": [{{"input": "...", "reason": "..."}}]}}'
    )


def _parse_authored(raw: str, start_id: int) -> list[Example]:
    """Parse LLM response into Example objects."""
    data = json.loads(raw)
    candidates = data["candidates"][:MAX_NEW_CANDIDATES]
    return [
        Example(
            id=f"authored-{start_id + i}",
            input=str(c["input"]),
            label="violation",
            reason=str(c["reason"]),
        )
        for i, c in enumerate(candidates)
    ]


def author_candidates(
    client: Any,
    rule_name: str,
    fn_texts: list[str],
    existing_ids: list[str],
) -> list[Example]:
    """Use LLM to batch-generate new candidates targeting FN cluster."""
    if not fn_texts:
        return []

    start_id = _next_candidate_id(existing_ids)
    prompt = _build_author_prompt(rule_name, fn_texts)

    try:
        response = client.messages.create(
            model=AUTHOR_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = str(response.content[0].text)
        return _parse_authored(raw, start_id)
    except Exception:
        logger.warning("Candidate authoring failed")
        return []


def write_candidates(path: str, candidates: list[Example]) -> None:
    """Append candidates to a YAML file with provenance."""
    existing: list[dict[str, str]] = []
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            existing = data.get("candidates", [])
    except FileNotFoundError:
        pass

    ts = datetime.now(timezone.utc).isoformat()
    for c in candidates:
        existing.append(
            {
                "id": c.id,
                "input": c.input,
                "label": c.label,
                "reason": c.reason,
                "authored_at": ts,
            }
        )

    with open(path, "w") as f:
        yaml.dump({"candidates": existing}, f, default_flow_style=False)


def load_candidates(path: str) -> list[Example]:
    """Load candidates from a YAML file."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return []

    if not isinstance(data, dict):
        return []

    raw = data.get("candidates", [])
    required = ("id", "input", "label", "reason")
    return [
        Example(
            id=str(c["id"]),
            input=str(c["input"]),
            label=str(c["label"]),
            reason=str(c["reason"]),
        )
        for c in raw
        if isinstance(c, dict) and all(k in c for k in required)
    ]


def inject_candidates(
    rule: Rule,
    new_candidates: list[Example],
) -> Rule:
    """Return a new Rule with candidates extended."""
    return Rule(
        name=rule.name,
        event=rule.event,
        prompt=rule.prompt,
        context=rule.context,
        action=rule.action,
        message=rule.message,
        threshold=rule.threshold,
        examples=rule.examples,
        candidates=rule.candidates + new_candidates,
    )
