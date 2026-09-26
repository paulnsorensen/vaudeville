"""Orchestrator prompt docs describe the typed rule schema, not the legacy one."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


class TestGeneratePromptSchema:
    def test_rule_yaml_block_uses_typed_fields(self) -> None:
        doc = _read("commands/generate/RALPH.md")
        for field in ("type: decide", "outcomes:", '"on":', "tier:", "test_cases:"):
            assert field in doc, f"generate RALPH.md missing {field!r}"

    def test_rule_yaml_block_has_no_legacy_fields(self) -> None:
        doc = _read("commands/generate/RALPH.md").lower()
        for legacy in ("threshold:", "label:", "tests/<", "phi-4", " phi "):
            assert legacy not in doc, f"generate RALPH.md still has legacy {legacy!r}"

    def test_test_cases_use_outcome_field(self) -> None:
        doc = _read("commands/generate/RALPH.md")
        assert "outcome: violation" in doc
        assert "outcome: clean" in doc


class TestSlmRuleWriterSchema:
    def test_no_phi_reference(self) -> None:
        doc = _read("agents/slm-rule-writer.md").lower()
        assert "phi" not in doc

    def test_no_legacy_test_file_reference(self) -> None:
        doc = _read("agents/slm-rule-writer.md")
        assert "tests/<" not in doc
        assert "label:" not in doc.lower()


class TestDesignPromptListsUnsure:
    def test_unsure_is_a_tuning_lever(self) -> None:
        doc = _read("commands/design/RALPH.md")
        assert "unsure" in doc.lower()


class TestRuleAuditSkillSchema:
    def test_no_phi_reference(self) -> None:
        doc = _read("skills/rule-audit/SKILL.md").lower()
        assert "phi-4" not in doc
