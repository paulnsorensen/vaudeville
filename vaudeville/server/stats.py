"""Aggregate classification events from JSONL logs.

Reads ``events.jsonl`` and produces summary statistics: per-rule
breakdowns, latency percentiles, and histogram buckets.
"""

from __future__ import annotations

import json
import os
import statistics
from typing import Any


_HISTOGRAM_BUCKETS = [50, 100, 200, 500, 1000]


def aggregate_events(log_path: str) -> dict[str, Any]:
    """Read *log_path* and return aggregated statistics.

    Returns a dict with keys: ``total``, ``rules``, ``latency``,
    ``time_range``.  Handles missing/empty files and malformed lines
    gracefully.
    """
    events = _parse_events(log_path)
    if not events:
        return _empty_result()

    valid = [e for e in events if "latency_ms" in e and "ts" in e]
    if not valid:
        return _empty_result()

    return {
        "total": len(valid),
        "rules": _summarize_rules(valid),
        "latency": _latency_stats([e["latency_ms"] for e in valid]),
        "time_range": {
            "earliest": min(e["ts"] for e in valid),
            "latest": max(e["ts"] for e in valid),
        },
    }


def _summarize_rules(
    events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Group events by rule and compute per-rule summaries."""
    rules: dict[str, dict[str, Any]] = {}
    for evt in events:
        rule = evt.get("rule", "<unknown>")
        if rule not in rules:
            rules[rule] = {"total": 0, "violations": 0, "latencies": []}
        rules[rule]["total"] += 1
        if evt.get("verdict") == "violation":
            rules[rule]["violations"] += 1
        rules[rule]["latencies"].append(evt["latency_ms"])

    summaries: dict[str, dict[str, Any]] = {}
    for name, data in sorted(rules.items()):
        total = data["total"]
        violations = data["violations"]
        pass_rate = ((total - violations) / total) * 100 if total else 0.0
        summaries[name] = {
            "total": total,
            "violations": violations,
            "pass_rate": round(pass_rate, 1),
            "avg_latency_ms": round(statistics.mean(data["latencies"]), 1),
        }
    return summaries


def _parse_events(log_path: str) -> list[dict[str, Any]]:
    """Parse JSONL lines, skipping malformed entries."""
    if not os.path.exists(log_path):
        return []
    events: list[dict[str, Any]] = []
    with open(log_path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    return events


def _latency_stats(latencies: list[float]) -> dict[str, Any]:
    """Compute percentiles, mean, and histogram buckets."""
    sorted_lat = sorted(latencies)
    n = len(sorted_lat)

    if n == 1:
        p50 = p95 = sorted_lat[0]
    else:
        quantiles = statistics.quantiles(sorted_lat, n=100)
        p50 = quantiles[49]
        p95 = quantiles[94]

    histogram: dict[str, int] = {}
    for bucket in _HISTOGRAM_BUCKETS:
        histogram[f"<={bucket}ms"] = 0
    histogram[f">{_HISTOGRAM_BUCKETS[-1]}ms"] = 0

    for lat in sorted_lat:
        placed = False
        for bucket in _HISTOGRAM_BUCKETS:
            if lat <= bucket:
                histogram[f"<={bucket}ms"] += 1
                placed = True
                break
        if not placed:
            histogram[f">{_HISTOGRAM_BUCKETS[-1]}ms"] += 1

    return {
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "mean_ms": round(statistics.mean(latencies), 1),
        "histogram": histogram,
    }


def _empty_result() -> dict[str, Any]:
    """Return a zero-value result for empty/missing logs."""
    histogram: dict[str, int] = {}
    for bucket in _HISTOGRAM_BUCKETS:
        histogram[f"<={bucket}ms"] = 0
    histogram[f">{_HISTOGRAM_BUCKETS[-1]}ms"] = 0

    return {
        "total": 0,
        "rules": {},
        "latency": {
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "mean_ms": 0.0,
            "histogram": histogram,
        },
        "time_range": {
            "earliest": "",
            "latest": "",
        },
    }
