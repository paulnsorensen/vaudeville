"""Orchestrator prompt docs describe the typed rule schema, not the legacy one."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from vaudeville.rules import parse_rule

ROOT = Path(__file__).resolve().parent.parent

_PHI = re.compile(r"\bphi\b", re.IGNORECASE)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


def _fenced_yaml_block(doc: str) -> str:
    match = re.search(r"```yaml\n(.*?)```", doc, re.DOTALL)
    assert match is not None, "no fenced yaml block found"
    return match.group(1)


class TestGeneratePromptSchema:
    def test_rule_yaml_block_uses_typed_fields(self) -> None:
        doc = _read("commands/generate/RALPH.md")
        for field in ("type: decide", "outcomes:", '"on":', "tier:", "test_cases:"):
            assert field in doc, f"generate RALPH.md missing {field!r}"

    def test_rule_yaml_block_has_no_legacy_fields(self) -> None:
        doc = _read("commands/generate/RALPH.md").lower()
        for legacy in ("threshold:", "label:", "tests/<", "phi-4"):
            assert legacy not in doc, f"generate RALPH.md still has legacy {legacy!r}"
        assert not _PHI.search(doc), "generate RALPH.md still references Phi"

    def test_no_text_placeholder(self) -> None:
        doc = _read("commands/generate/RALPH.md")
        assert "{text}" not in doc, "generate RALPH.md still teaches the {text} placeholder"

    def test_test_cases_use_outcome_field(self) -> None:
        doc = _read("commands/generate/RALPH.md")
        assert "outcome: violation" in doc
        assert "outcome: clean" in doc

    def test_rule_yaml_block_parses_as_a_valid_decide_rule(self) -> None:
        doc = _read("commands/generate/RALPH.md")
        block = _fenced_yaml_block(doc)
        data = yaml.safe_load(block)
        rule = parse_rule(data)
        assert rule.type == "decide"
        assert rule.outcomes == ["violation", "clean"]


class TestSlmRuleWriterSchema:
    def test_no_phi_reference(self) -> None:
        doc = _read("agents/slm-rule-writer.md")
        assert not _PHI.search(doc), "slm-rule-writer.md still references Phi"

    def test_no_legacy_test_file_reference(self) -> None:
        doc = _read("agents/slm-rule-writer.md")
        assert "tests/<" not in doc
        assert "examples/tests" not in doc
        assert "label:" not in doc.lower()

    def test_no_text_placeholder(self) -> None:
        doc = _read("agents/slm-rule-writer.md")
        assert "{text}" not in doc, "slm-rule-writer.md still teaches the {text} placeholder"

    def test_only_shipped_example_rules_named(self) -> None:
        doc = _read("agents/slm-rule-writer.md")
        shipped = {p.stem for p in (ROOT / "examples" / "rules").glob("*.yaml")}
        assert shipped, "no shipped example rules found to check against"
        section = doc.split("## Style Reference", 1)[1].split("## Gotchas", 1)[0]
        named = re.findall(r"`([a-z0-9-]+)`", section)
        assert named, "no example rule names found under ## Style Reference"
        for name in named:
            assert name in shipped, f"slm-rule-writer.md cites unshipped example {name!r}"


class TestDesignPromptListsUnsure:
    def test_unsure_is_a_tuning_lever(self) -> None:
        doc = _read("commands/design/RALPH.md")
        assert "unsure:" in doc
        assert "below" in doc

    def test_no_threshold_lever(self) -> None:
        doc = _read("commands/design/RALPH.md")
        assert "adjust threshold" not in doc.lower()


class TestRuleAuditSkillSchema:
    def test_no_phi_reference(self) -> None:
        doc = _read("skills/rule-audit/SKILL.md").lower()
        assert "phi-4" not in doc
