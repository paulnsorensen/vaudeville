"""Tests for vaudeville.server.stats."""

from __future__ import annotations

import json
import pathlib

from vaudeville.server.stats import aggregate_events


def _write_events(path: pathlib.Path, events: list[dict[str, object]]) -> str:
    """Write JSONL events to a file and return the path string."""
    log_file = path / "events.jsonl"
    lines = [json.dumps(e) for e in events]
    log_file.write_text("\n".join(lines) + "\n")
    return str(log_file)


def _make_event(
    rule: str = "no-hedging",
    verdict: str = "clean",
    confidence: float = 0.9,
    latency_ms: float = 50.0,
    prompt_chars: int = 100,
    ts: str = "2026-04-12T10:00:00+00:00",
) -> dict[str, object]:
    return {
        "ts": ts,
        "rule": rule,
        "verdict": verdict,
        "confidence": confidence,
        "latency_ms": latency_ms,
        "prompt_chars": prompt_chars,
    }


def test_missing_file(tmp_path: pathlib.Path) -> None:
    """Missing log file returns zero-value result."""
    result = aggregate_events(str(tmp_path / "nonexistent.jsonl"))
    assert result["total"] == 0
    assert result["rules"] == {}
    assert result["latency"]["p50_ms"] == 0.0
    assert result["time_range"]["earliest"] == ""


def test_empty_file(tmp_path: pathlib.Path) -> None:
    """Empty log file returns zero-value result."""
    log_file = tmp_path / "events.jsonl"
    log_file.write_text("")
    result = aggregate_events(str(log_file))
    assert result["total"] == 0


def test_malformed_lines_skipped(tmp_path: pathlib.Path) -> None:
    """Malformed JSON lines are skipped without error."""
    log_file = tmp_path / "events.jsonl"
    good = json.dumps(_make_event())
    log_file.write_text(f"not-json\n{good}\n{{bad\n")
    result = aggregate_events(str(log_file))
    assert result["total"] == 1


def test_single_event(tmp_path: pathlib.Path) -> None:
    """Single event produces correct aggregation."""
    path = _write_events(tmp_path, [_make_event(latency_ms=42.0)])
    result = aggregate_events(path)

    assert result["total"] == 1
    assert "no-hedging" in result["rules"]
    rule = result["rules"]["no-hedging"]
    assert rule["total"] == 1
    assert rule["violations"] == 0
    assert rule["pass_rate"] == 100.0
    assert rule["avg_latency_ms"] == 42.0


def test_per_rule_breakdown(tmp_path: pathlib.Path) -> None:
    """Multiple rules produce separate breakdowns."""
    events = [
        _make_event(rule="no-hedging", verdict="clean"),
        _make_event(rule="no-hedging", verdict="violation"),
        _make_event(rule="no-sycophancy", verdict="clean"),
    ]
    path = _write_events(tmp_path, events)
    result = aggregate_events(path)

    assert result["total"] == 3
    hedging = result["rules"]["no-hedging"]
    assert hedging["total"] == 2
    assert hedging["violations"] == 1
    assert hedging["pass_rate"] == 50.0

    syc = result["rules"]["no-sycophancy"]
    assert syc["total"] == 1
    assert syc["violations"] == 0
    assert syc["pass_rate"] == 100.0


def test_latency_percentiles(tmp_path: pathlib.Path) -> None:
    """Latency p50/p95/mean are computed correctly."""
    events = [_make_event(latency_ms=float(i)) for i in range(1, 101)]
    path = _write_events(tmp_path, events)
    result = aggregate_events(path)

    lat = result["latency"]
    assert lat["p50_ms"] == 50.5
    assert lat["p95_ms"] == 96.0
    assert lat["mean_ms"] == 50.5


def test_latency_histogram(tmp_path: pathlib.Path) -> None:
    """Latency histogram buckets are populated correctly."""
    events = [
        _make_event(latency_ms=25.0),  # <=50
        _make_event(latency_ms=50.0),  # <=50
        _make_event(latency_ms=75.0),  # <=100
        _make_event(latency_ms=150.0),  # <=200
        _make_event(latency_ms=300.0),  # <=500
        _make_event(latency_ms=800.0),  # <=1000
        _make_event(latency_ms=1500.0),  # >1000
    ]
    path = _write_events(tmp_path, events)
    result = aggregate_events(path)

    hist = result["latency"]["histogram"]
    assert hist["<=50ms"] == 2
    assert hist["<=100ms"] == 1
    assert hist["<=200ms"] == 1
    assert hist["<=500ms"] == 1
    assert hist["<=1000ms"] == 1
    assert hist[">1000ms"] == 1


def test_time_range(tmp_path: pathlib.Path) -> None:
    """Time range captures earliest and latest timestamps."""
    events = [
        _make_event(ts="2026-04-12T08:00:00+00:00"),
        _make_event(ts="2026-04-12T12:00:00+00:00"),
        _make_event(ts="2026-04-12T10:00:00+00:00"),
    ]
    path = _write_events(tmp_path, events)
    result = aggregate_events(path)

    assert result["time_range"]["earliest"] == "2026-04-12T08:00:00+00:00"
    assert result["time_range"]["latest"] == "2026-04-12T12:00:00+00:00"


def test_rules_sorted_alphabetically(tmp_path: pathlib.Path) -> None:
    """Rule summaries are sorted by name."""
    events = [
        _make_event(rule="zebra"),
        _make_event(rule="alpha"),
        _make_event(rule="middle"),
    ]
    path = _write_events(tmp_path, events)
    result = aggregate_events(path)

    assert list(result["rules"].keys()) == ["alpha", "middle", "zebra"]


def test_all_violations(tmp_path: pathlib.Path) -> None:
    """Pass rate is 0% when all events are violations."""
    events = [
        _make_event(verdict="violation"),
        _make_event(verdict="violation"),
    ]
    path = _write_events(tmp_path, events)
    result = aggregate_events(path)

    rule = result["rules"]["no-hedging"]
    assert rule["pass_rate"] == 0.0
    assert rule["violations"] == 2


def test_blank_lines_ignored(tmp_path: pathlib.Path) -> None:
    """Blank lines in the JSONL file are skipped."""
    log_file = tmp_path / "events.jsonl"
    good = json.dumps(_make_event())
    log_file.write_text(f"\n{good}\n\n{good}\n\n")
    result = aggregate_events(str(log_file))
    assert result["total"] == 2


def test_partial_records_skipped(tmp_path: pathlib.Path) -> None:
    """Events missing required fields are skipped gracefully."""
    log_file = tmp_path / "events.jsonl"
    partial = json.dumps({"rule": "x", "verdict": "clean"})
    good = json.dumps(_make_event())
    log_file.write_text(f"{partial}\n{good}\n")
    result = aggregate_events(str(log_file))
    assert result["total"] == 1


def test_all_partial_records_returns_empty(tmp_path: pathlib.Path) -> None:
    """If all records lack required fields, return empty result."""
    log_file = tmp_path / "events.jsonl"
    log_file.write_text(json.dumps({"rule": "x"}) + "\n")
    result = aggregate_events(str(log_file))
    assert result["total"] == 0


def test_single_event_latency_percentiles(tmp_path: pathlib.Path) -> None:
    """Single event has equal p50 and p95."""
    path = _write_events(tmp_path, [_make_event(latency_ms=42.0)])
    result = aggregate_events(path)

    lat = result["latency"]
    assert lat["p50_ms"] == 42.0
    assert lat["p95_ms"] == 42.0
    assert lat["mean_ms"] == 42.0
