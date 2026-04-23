"""Tests for vaudeville.orchestrator.abandon_rule."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestAbandonRule:
    """Test rule abandonment: tier update, file search, reason logging."""

    def test_abandon_sets_tier_disabled_in_rules_dir(self, tmp_path: Path) -> None:
        """Abandon updates tier to disabled in the specified rules_dir."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        abandon_rule("test-rule", "test reason", {}, str(rules_dir))

        content = rule_file.read_text()
        assert "tier: disabled" in content
        assert "ABANDONED" in content

    def test_abandon_log_dir_derived_from_rules_dir(self, tmp_path: Path) -> None:
        """log_dir is derived as rules_dir/../logs."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\n")

        abandon_rule("test-rule", "stale", {}, str(rules_dir))

        log_file = tmp_path / ".vaudeville" / "logs" / "abandoned.jsonl"
        assert log_file.exists()

    def test_abandon_appends_reason_comment(self, tmp_path: Path) -> None:
        """Abandon appends ISO UTC timestamp and reason as YAML comment."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        abandon_rule("test-rule", "rule is impossible", {}, str(rules_dir))

        content = rule_file.read_text()
        assert "# ABANDONED" in content
        assert "rule is impossible" in content

    def test_abandon_flattens_newlines_in_reason(self, tmp_path: Path) -> None:
        """Abandon converts newlines in reason to spaces."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        reason = "first line\nsecond line\nthird line"
        abandon_rule("test-rule", reason, {}, str(rules_dir))

        content = rule_file.read_text()
        assert "first line second line third line" in content

    def test_abandon_writes_jsonl_log(self, tmp_path: Path) -> None:
        """Abandon appends one JSON line to .vaudeville/logs/abandoned.jsonl."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        metrics: dict[str, object] = {"precision": 0.8, "recall": 0.75}
        abandon_rule("test-rule", "stagnant", metrics, str(rules_dir))

        log_file = tmp_path / ".vaudeville" / "logs" / "abandoned.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[-1])
        assert entry["rule"] == "test-rule"
        assert entry["reason"] == "stagnant"
        assert entry["metrics"] == metrics

    def test_abandon_no_trailing_newline_produces_valid_yaml(
        self, tmp_path: Path
    ) -> None:
        """Rule file without trailing newline gets a newline inserted before appended tier."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_bytes(b"name: test-rule\nprompt: test")  # no trailing newline

        abandon_rule("test-rule", "stagnant", {}, str(rules_dir))

        content = rule_file.read_text()
        assert "tier: disabled" in content
        lines = content.splitlines()
        tier_line = next(line for line in lines if line.startswith("tier:"))
        assert tier_line == "tier: disabled"

    def test_abandon_creates_log_dir_if_missing(self, tmp_path: Path) -> None:
        """Abandon creates .vaudeville/logs/ if it doesn't exist."""
        from vaudeville.orchestrator import abandon_rule

        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        rule_file = rules_dir / "test-rule.yaml"
        rule_file.write_text("name: test-rule\ntier: shadow\nprompt: test\n")

        abandon_rule("test-rule", "test", {}, str(rules_dir))

        log_dir = tmp_path / ".vaudeville" / "logs"
        assert log_dir.exists()

    def test_abandon_missing_rule_file_raises_filenotfound(
        self, tmp_path: Path
    ) -> None:
        """Abandon raises FileNotFoundError if rule file not found in rules_dir."""
        from vaudeville.orchestrator import abandon_rule

        nonexistent_rules_dir = tmp_path / ".vaudeville" / "rules"

        with pytest.raises(FileNotFoundError):
            abandon_rule("nonexistent-rule", "test", {}, str(nonexistent_rules_dir))
