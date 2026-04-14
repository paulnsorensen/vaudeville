"""Tests for tier-advisor scripts: ingest, analyze, report."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "skills" / "tier-advisor" / "scripts"


def _load_module(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ingest_mod() -> Any:
    return _load_module("ingest")


@pytest.fixture
def analyze_mod() -> Any:
    return _load_module("analyze")


@pytest.fixture
def report_mod() -> Any:
    return _load_module("report")


@pytest.fixture
def tmp_logs(tmp_path: Path) -> Path:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    return logs_dir


@pytest.fixture
def events_data() -> list[dict[str, Any]]:
    return [
        {
            "ts": "2026-04-10T10:00:00+00:00",
            "rule": "violation-detector",
            "verdict": "clean",
            "confidence": 0.85,
            "latency_ms": 120.0,
            "prompt_chars": 500,
        },
        {
            "ts": "2026-04-10T10:01:00+00:00",
            "rule": "violation-detector",
            "verdict": "violation",
            "confidence": 0.92,
            "latency_ms": 130.0,
            "prompt_chars": 600,
        },
    ]


class TestIngest:
    def test_read_jsonl(
        self, ingest_mod: Any, tmp_logs: Path, events_data: list[dict[str, Any]]
    ) -> None:
        events_file = tmp_logs / "events.jsonl"
        with open(events_file, "w") as f:
            for row in events_data:
                f.write(json.dumps(row) + "\n")

        result = ingest_mod.read_jsonl(events_file)
        assert len(result) == 2
        assert result[0]["rule"] == "violation-detector"

    def test_read_jsonl_missing_file(self, ingest_mod: Any, tmp_logs: Path) -> None:
        result = ingest_mod.read_jsonl(tmp_logs / "nonexistent.jsonl")
        assert result == []

    def test_read_jsonl_malformed_lines(self, ingest_mod: Any, tmp_logs: Path) -> None:
        f = tmp_logs / "bad.jsonl"
        f.write_text('{"valid": true}\nnot json\n{"also": "valid"}\n')
        result = ingest_mod.read_jsonl(f)
        assert len(result) == 2

    def test_snippet_hash_deterministic(self, ingest_mod: Any) -> None:
        h1 = ingest_mod.snippet_hash("test input")
        h2 = ingest_mod.snippet_hash("test input")
        assert h1 == h2
        assert len(h1) == 16

    def test_snippet_hash_none(self, ingest_mod: Any) -> None:
        h = ingest_mod.snippet_hash(None)
        assert len(h) == 16

    def test_build_records_deduplicates(
        self, ingest_mod: Any, tmp_logs: Path, events_data: list[dict[str, Any]]
    ) -> None:
        events_file = tmp_logs / "events.jsonl"
        with open(events_file, "w") as f:
            for row in events_data:
                f.write(json.dumps(row) + "\n")
            for row in events_data:
                f.write(json.dumps(row) + "\n")

        violations_file = tmp_logs / "violations.jsonl"
        violations_file.write_text("")

        with (
            patch.object(ingest_mod, "EVENTS_FILE", events_file),
            patch.object(ingest_mod, "VIOLATIONS_FILE", violations_file),
        ):
            records = ingest_mod.build_records()
        assert len(records) == 2


class TestAnalyze:
    def test_find_next_user_message(self, analyze_mod: Any) -> None:
        msgs = [
            ("2026-04-10T09:00:00Z", "earlier"),
            ("2026-04-10T10:05:00Z", "after violation"),
            ("2026-04-10T10:10:00Z", "later"),
        ]
        result = analyze_mod._find_next_user_message("2026-04-10T10:00:00Z", msgs)
        assert result == "after violation"

    def test_find_next_user_message_none(self, analyze_mod: Any) -> None:
        msgs = [("2026-04-10T09:00:00Z", "earlier")]
        result = analyze_mod._find_next_user_message("2026-04-10T10:00:00Z", msgs)
        assert result is None


class TestReport:
    def _make_rule(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "rule": "test-rule",
            "total_evals": 100,
            "violations": 15,
            "cleans": 85,
            "violation_rate": 0.15,
            "avg_confidence": 0.85,
            "p50_confidence": 0.80,
            "first_seen": "2026-04-01T00:00:00Z",
            "last_seen": "2026-04-13T00:00:00Z",
            "agreement_rate": 0.80,
            "agreement_evaluated": 10,
            "agreement_agreed": 8,
            "agreement_disagreed": 2,
            "agreement_uncertain": 5,
        }
        base.update(overrides)
        return base

    def test_classify_insufficient_data(self, report_mod: Any) -> None:
        with patch.object(report_mod, "get_current_tier", return_value="shadow"):
            rec, reason = report_mod.classify(self._make_rule(total_evals=5))
        assert rec == "insufficient-data"
        assert "5 evals" in reason

    def test_classify_shadow_to_warn(self, report_mod: Any) -> None:
        with patch.object(report_mod, "get_current_tier", return_value="shadow"):
            rec, _ = report_mod.classify(
                self._make_rule(
                    total_evals=60, violation_rate=0.15, agreement_rate=0.75
                )
            )
        assert rec == "promote-to-warn"

    def test_classify_shadow_low_agreement(self, report_mod: Any) -> None:
        with patch.object(report_mod, "get_current_tier", return_value="shadow"):
            rec, _ = report_mod.classify(
                self._make_rule(
                    total_evals=60, violation_rate=0.15, agreement_rate=0.50
                )
            )
        assert rec == "hold-at-shadow"

    def test_classify_warn_demote_low_agreement(self, report_mod: Any) -> None:
        with patch.object(report_mod, "get_current_tier", return_value="warn"):
            rec, _ = report_mod.classify(self._make_rule(agreement_rate=0.30))
        assert rec == "demote"

    def test_classify_warn_demote_high_violation_rate(self, report_mod: Any) -> None:
        with patch.object(report_mod, "get_current_tier", return_value="warn"):
            rec, _ = report_mod.classify(
                self._make_rule(violation_rate=0.70, agreement_rate=0.80)
            )
        assert rec == "demote"

    def test_classify_warn_to_block(self, report_mod: Any) -> None:
        with patch.object(report_mod, "get_current_tier", return_value="warn"):
            rec, _ = report_mod.classify(
                self._make_rule(
                    total_evals=250,
                    violation_rate=0.15,
                    agreement_rate=0.90,
                    p50_confidence=0.80,
                )
            )
        assert rec == "promote-to-block"

    def test_classify_hold_at_warn(self, report_mod: Any) -> None:
        with patch.object(report_mod, "get_current_tier", return_value="warn"):
            rec, _ = report_mod.classify(
                self._make_rule(
                    total_evals=80,
                    violation_rate=0.15,
                    agreement_rate=0.75,
                )
            )
        assert rec == "hold-at-warn"

    def test_classify_violation_rate_too_high_for_shadow(self, report_mod: Any) -> None:
        with patch.object(report_mod, "get_current_tier", return_value="shadow"):
            rec, _ = report_mod.classify(
                self._make_rule(
                    total_evals=60, violation_rate=0.50, agreement_rate=0.80
                )
            )
        assert rec == "hold-at-shadow"

    def test_format_report_produces_markdown(self, report_mod: Any) -> None:
        rules = [
            self._make_rule(rule="my-rule", total_evals=60, violation_rate=0.15),
        ]
        with patch.object(report_mod, "get_current_tier", return_value="shadow"):
            report = report_mod.format_report(rules)
        assert "# Tier Advisor Report" in report
        assert "my-rule" in report
        assert "Promote to Warn" in report

    def test_get_current_tier_reads_yaml(self, report_mod: Any, tmp_path: Path) -> None:
        rules_dir = tmp_path / "rules_dev"
        rules_dir.mkdir()
        (rules_dir / "test-rule.yaml").write_text("name: test-rule\ntier: warn\n")

        with patch.object(report_mod, "RULES_DEV_DIR", rules_dir):
            tier = report_mod.get_current_tier("test-rule")
        assert tier == "warn"

    def test_get_current_tier_missing_file(
        self, report_mod: Any, tmp_path: Path
    ) -> None:
        with patch.object(report_mod, "RULES_DEV_DIR", tmp_path):
            tier = report_mod.get_current_tier("nonexistent")
        assert tier == "unknown"
