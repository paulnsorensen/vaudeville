"""Tests for vaudeville.rules directory and layered loading (AC-1, AC-12)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from vaudeville.rules import (
    DecideRule,
    RewriteRule,
    RuleSet,
    load_rule_file,
    load_rules,
    load_rules_layered,
)

DECIDE_RULE: dict[str, Any] = {
    "type": "decide",
    "name": "git-gate",
    "event": "Stop",
    "prompt": "classify",
    "outcomes": ["violation", "clean"],
    "on": {"violation": "block"},
}

REWRITE_RULE: dict[str, Any] = {
    "type": "rewrite",
    "name": "trim",
    "event": "PreToolUse",
    "matcher": "Write",
    "prompt": "trim it",
    "target": ["tool_input.content"],
}


def _write_rule(directory: Path, filename: str, data: dict[str, Any]) -> Path:
    path = directory / filename
    path.write_text(yaml.dump(data))
    return path


class TestLoadRuleFile:
    def test_loads_decide_rule(self, tmp_path: Path) -> None:
        path = _write_rule(tmp_path, "gate.yaml", DECIDE_RULE)
        rule = load_rule_file(path)
        assert isinstance(rule, DecideRule)
        assert rule.name == "git-gate"

    def test_draft_rule_returns_none(self, tmp_path: Path) -> None:
        data = dict(DECIDE_RULE, draft=True)
        path = _write_rule(tmp_path, "draft.yaml", data)
        assert load_rule_file(path) is None

    def test_non_mapping_yaml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- a\n- b\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_rule_file(path)


class TestProjectRuleArgvRejected:
    def test_project_rule_argv_rejected(self, tmp_path: Path) -> None:
        data = dict(DECIDE_RULE, argv=["rm", "-rf", "/"])
        path = _write_rule(tmp_path, "malicious.yaml", data)
        with pytest.raises(ValueError, match="argv"):
            load_rule_file(path)

    def test_command_key_rejected(self, tmp_path: Path) -> None:
        data = dict(DECIDE_RULE, command="curl evil.example")
        path = _write_rule(tmp_path, "malicious2.yaml", data)
        with pytest.raises(ValueError, match="command"):
            load_rule_file(path)


class TestLoadRulesDirectory:
    def test_invalid_rule_skipped_while_others_load(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_rule(tmp_path, "good.yaml", DECIDE_RULE)
        _write_rule(tmp_path, "bad.yaml", dict(DECIDE_RULE, name="bad", type="nope"))
        with caplog.at_level("WARNING"):
            rules = load_rules(str(tmp_path))
        assert set(rules) == {"git-gate"}
        assert "bad.yaml" in caplog.text

    def test_missing_directory_returns_empty(self, tmp_path: Path) -> None:
        assert load_rules(str(tmp_path / "nope")) == {}

    def test_draft_rule_skipped_while_others_load(self, tmp_path: Path) -> None:
        _write_rule(tmp_path, "good.yaml", DECIDE_RULE)
        _write_rule(
            tmp_path, "draft.yaml", dict(DECIDE_RULE, name="draft-rule", draft=True)
        )

        rules = load_rules(str(tmp_path))
        assert set(rules) == {"git-gate"}


class TestLoadRulesLayered:
    def test_bundled_layer_loads_below_user_and_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plugin_root = tmp_path / "plugin"
        (plugin_root / "examples" / "rules").mkdir(parents=True)
        _write_rule(plugin_root / "examples" / "rules", "bundled.yaml", REWRITE_RULE)

        home = tmp_path / "home"
        (home / ".vaudeville" / "rules").mkdir(parents=True)

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

        ruleset = load_rules_layered(None)
        rules = ruleset.by_name()
        assert isinstance(rules["trim"], RewriteRule)


class TestProjectLayerTrust:
    def test_project_rule_same_name_as_user_is_skipped_and_logged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        home = tmp_path / "home"
        (home / ".vaudeville" / "rules").mkdir(parents=True)
        _write_rule(
            home / ".vaudeville" / "rules",
            "gate.yaml",
            dict(DECIDE_RULE, tier="shadow"),
        )

        project = tmp_path / "project"
        (project / ".vaudeville" / "rules").mkdir(parents=True)
        _write_rule(
            project / ".vaudeville" / "rules",
            "gate.yaml",
            dict(DECIDE_RULE, tier="block"),
        )

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "empty-plugin-root"))

        with caplog.at_level("WARNING"):
            ruleset = load_rules_layered(str(project))
        rules = ruleset.by_name()
        assert rules["git-gate"].tier == "shadow"
        assert "git-gate" in caplog.text
        assert "user" in caplog.text

    def test_invalid_user_rule_still_blocks_project_rule_of_same_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        home = tmp_path / "home"
        (home / ".vaudeville" / "rules").mkdir(parents=True)
        _write_rule(
            home / ".vaudeville" / "rules",
            "bad.yaml",
            dict(DECIDE_RULE, name="x", type="nope"),
        )

        project = tmp_path / "project"
        (project / ".vaudeville" / "rules").mkdir(parents=True)
        _write_rule(
            project / ".vaudeville" / "rules",
            "good.yaml",
            dict(DECIDE_RULE, name="x"),
        )

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "empty-plugin-root"))

        with caplog.at_level("WARNING"):
            ruleset = load_rules_layered(str(project))

        assert "x" not in ruleset.by_name()
        assert "bad.yaml" in caplog.text
        assert "good.yaml" in caplog.text

    def test_project_only_rule_still_loads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        (home / ".vaudeville" / "rules").mkdir(parents=True)

        project = tmp_path / "project"
        (project / ".vaudeville" / "rules").mkdir(parents=True)
        _write_rule(project / ".vaudeville" / "rules", "gate.yaml", DECIDE_RULE)

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "empty-plugin-root"))

        ruleset = load_rules_layered(str(project))
        assert "git-gate" in ruleset.by_name()


class TestObsoleteContextKey:
    def test_rule_with_context_key_skipped_and_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_rule(tmp_path, "good.yaml", DECIDE_RULE)
        _write_rule(
            tmp_path,
            "has-context.yaml",
            dict(DECIDE_RULE, name="has-context", context="legacy"),
        )
        with caplog.at_level("WARNING"):
            rules = load_rules(str(tmp_path))
        assert set(rules) == {"git-gate"}
        assert "context" in caplog.text


class TestRuleSetForEvent:
    def test_for_event_filters_by_event(self, tmp_path: Path) -> None:
        decide_path = _write_rule(tmp_path, "gate.yaml", DECIDE_RULE)
        rewrite_path = _write_rule(tmp_path, "trim.yaml", REWRITE_RULE)
        decide_rule = load_rule_file(decide_path)
        rewrite_rule = load_rule_file(rewrite_path)
        assert decide_rule is not None
        assert rewrite_rule is not None
        ruleset = RuleSet(rules=(decide_rule, rewrite_rule))

        assert ruleset.for_event("Stop") == [decide_rule]
        assert ruleset.for_event("PreToolUse") == [rewrite_rule]
        assert ruleset.for_event("PostToolUse") == []
