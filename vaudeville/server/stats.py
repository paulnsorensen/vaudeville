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


def _empty_histogram() -> dict[str, int]:
    h: dict[str, int] = {f"<={b}ms": 0 for b in _HISTOGRAM_BUCKETS}
    h[f">{_HISTOGRAM_BUCKETS[-1]}ms"] = 0
    return h


def aggregate_events(log_path: str) -> dict[str, Any]:
    """Read *log_path* and return aggregated statistics.

    Returns a dict with keys: ``total``, ``rules``, ``latency``,
    ``time_range``.  Handles missing/empty files and malformed lines
    gracefully.
    """
    events = _parse_events(log_path)
    if not events:
        return empty_result()

    valid = [e for e in events if "latency_ms" in e and "ts" in e]
    if not valid:
        return empty_result()

    return {
        "total": len(valid),
        "rules": _summarize_rules(valid),
        "latency": _latency_stats([e["latency_ms"] for e in valid]),
        "time_range": {
            "earliest": min(e["ts"] for e in valid),
            "latest": max(e["ts"] for e in valid),
        },
    }


def _percentiles(latencies: list[float]) -> tuple[float, float]:
    """Return (p50, p95) for *latencies*. Handles the n==1 special case."""
    n = len(latencies)
    if n == 1:
        return latencies[0], latencies[0]
    quantiles = statistics.quantiles(sorted(latencies), n=100)
    return quantiles[49], quantiles[94]


def _summarize_rules(
    events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
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
        p50, p95 = _percentiles(data["latencies"])
        summaries[name] = {
            "total": total,
            "violations": violations,
            "pass_rate": round(pass_rate, 1),
            "avg_latency_ms": round(statistics.mean(data["latencies"]), 1),
            "p50_latency_ms": round(p50, 1),
            "p95_latency_ms": round(p95, 1),
        }
    return summaries


def _parse_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        evt: dict[str, Any] = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return evt


def _parse_events(log_path: str) -> list[dict[str, Any]]:
    if not os.path.exists(log_path):
        return []
    with open(log_path) as f:
        return [evt for line in f if (evt := _parse_line(line)) is not None]


def _bucket_for_latency(lat: float) -> str:
    for bucket in _HISTOGRAM_BUCKETS:
        if lat <= bucket:
            return f"<={bucket}ms"
    return f">{_HISTOGRAM_BUCKETS[-1]}ms"


def _latency_stats(latencies: list[float]) -> dict[str, Any]:
    sorted_lat = sorted(latencies)
    p50, p95 = _percentiles(sorted_lat)
    histogram = _empty_histogram()

    for lat in sorted_lat:
        histogram[_bucket_for_latency(lat)] += 1

    return {
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "mean_ms": round(statistics.mean(latencies), 1),
        "histogram": histogram,
    }


def empty_result() -> dict[str, Any]:
    return {
        "total": 0,
        "rules": {},
        "latency": {
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "mean_ms": 0.0,
            "histogram": _empty_histogram(),
        },
        "time_range": {
            "earliest": "",
            "latest": "",
        },
    }
