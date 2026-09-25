"""Tests for vaudeville.rules admin helpers on new-format rule files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from vaudeville.rules import (
    VALID_TIERS,
    DecideRule,
    get_draft_rule_names,
    list_rules_with_source,
    load_rules_layered,
    locate_all_rule_files,
    locate_rule_file,
    rules_search_path,
    set_tier,
    validate_rule_file,
)

DECIDE_RULE: dict[str, Any] = {
    "type": "decide",
    "name": "git-gate",
    "event": "Stop",
    "prompt": "classify",
    "outcomes": ["violation", "clean"],
    "on": {"violation": "block"},
    "tier": "shadow",
}


def _write_rule(directory: Path, filename: str, data: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(yaml.dump(data))
    return path


class TestSearchPathAndLocate:
    def test_rules_search_path_orders_global_then_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        (home / ".vaudeville" / "rules").mkdir(parents=True)
        project = tmp_path / "project"
        (project / ".vaudeville" / "rules").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))

        dirs = rules_search_path(str(project))
        assert dirs == [
            str(home / ".vaudeville" / "rules"),
            str(project / ".vaudeville" / "rules"),
        ]

    def test_locate_rule_file_prefers_user_over_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        _write_rule(home / ".vaudeville" / "rules", "git-gate.yaml", DECIDE_RULE)
        project = tmp_path / "project"
        _write_rule(project / ".vaudeville" / "rules", "git-gate.yaml", DECIDE_RULE)
        monkeypatch.setenv("HOME", str(home))

        path = locate_rule_file("git-gate", str(project))
        assert path == home / ".vaudeville" / "rules" / "git-gate.yaml"
        assert len(locate_all_rule_files("git-gate", str(project))) == 2

    def test_locate_rule_file_missing_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        with pytest.raises(FileNotFoundError):
            locate_rule_file("nope")


class TestSetTier:
    def test_set_tier_updates_existing_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        path = _write_rule(home / ".vaudeville" / "rules", "git-gate.yaml", DECIDE_RULE)
        monkeypatch.setenv("HOME", str(home))

        set_tier("git-gate", "block")

        rule = validate_rule_file(path)
        assert isinstance(rule, DecideRule)
        assert rule.tier == "block"

    def test_set_tier_rejects_invalid_tier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        _write_rule(home / ".vaudeville" / "rules", "git-gate.yaml", DECIDE_RULE)
        monkeypatch.setenv("HOME", str(home))

        with pytest.raises(ValueError, match="Invalid tier"):
            set_tier("git-gate", "nonsense")

    def test_set_tier_appends_when_field_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        data = {k: v for k, v in DECIDE_RULE.items() if k != "tier"}
        path = _write_rule(home / ".vaudeville" / "rules", "git-gate.yaml", data)
        monkeypatch.setenv("HOME", str(home))

        set_tier("git-gate", "warn")

        assert "tier: warn" in path.read_text()


class TestListAndDrafts:
    def test_list_rules_with_source_deduplicates_by_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home_rules = home / ".vaudeville" / "rules"
        _write_rule(home_rules, "git-gate.yaml", DECIDE_RULE)
        project = tmp_path / "project"
        project_rules = project / ".vaudeville" / "rules"
        _write_rule(project_rules, "git-gate.yaml", dict(DECIDE_RULE, tier="block"))
        monkeypatch.setenv("HOME", str(home))

        pairs = list_rules_with_source(str(project))
        assert len(pairs) == 1
        rule, source_dir = pairs[0]
        assert rule.tier != "block"
        assert source_dir == str(home_rules)

    def test_get_draft_rule_names(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / "rules"
        _write_rule(rules_dir, "draft.yaml", dict(DECIDE_RULE, draft=True))
        _write_rule(rules_dir, "live.yaml", DECIDE_RULE)

        names = get_draft_rule_names(str(rules_dir))
        assert names == {"git-gate"}

    def test_list_rules_with_source_skips_unreadable_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home_rules = home / ".vaudeville" / "rules"
        home_rules.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        real_listdir = os.listdir

        def _raising_listdir(path: str) -> list[str]:
            if path == str(home_rules):
                raise OSError("permission denied")
            return real_listdir(path)

        monkeypatch.setattr(os, "listdir", _raising_listdir)

        assert list_rules_with_source() == []

    def test_list_rules_with_source_skips_non_yaml_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home_rules = home / ".vaudeville" / "rules"
        home_rules.mkdir(parents=True)
        (home_rules / "README.md").write_text("not a rule")
        _write_rule(home_rules, "git-gate.yaml", DECIDE_RULE)
        monkeypatch.setenv("HOME", str(home))

        pairs = list_rules_with_source()
        assert len(pairs) == 1

    def test_list_rules_with_source_skips_rule_that_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home_rules = home / ".vaudeville" / "rules"
        _write_rule(home_rules, "bad.yaml", dict(DECIDE_RULE, name="bad", type="nope"))
        _write_rule(home_rules, "git-gate.yaml", DECIDE_RULE)
        monkeypatch.setenv("HOME", str(home))

        pairs = list_rules_with_source()
        assert len(pairs) == 1
        assert pairs[0][0].name == "git-gate"

    def test_list_rules_with_source_skips_draft_rule(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home_rules = home / ".vaudeville" / "rules"
        _write_rule(home_rules, "draft.yaml", dict(DECIDE_RULE, draft=True))
        monkeypatch.setenv("HOME", str(home))

        assert list_rules_with_source() == []

    def test_get_draft_rule_names_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert get_draft_rule_names(str(tmp_path / "missing")) == set()

    def test_get_draft_rule_names_skips_non_yaml_files(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "README.md").write_text("not yaml")
        _write_rule(rules_dir, "draft.yaml", dict(DECIDE_RULE, draft=True))

        names = get_draft_rule_names(str(rules_dir))
        assert names == {"git-gate"}

    def test_get_draft_rule_names_skips_files_that_raise(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "bad.yaml").mkdir()
        _write_rule(rules_dir, "draft.yaml", dict(DECIDE_RULE, draft=True))

        names = get_draft_rule_names(str(rules_dir))
        assert names == {"git-gate"}

    def test_validate_rule_file_raises_on_invalid(self, tmp_path: Path) -> None:
        path = _write_rule(tmp_path, "bad.yaml", dict(DECIDE_RULE, type="nope"))
        with pytest.raises(Exception):
            validate_rule_file(path)

    def test_valid_tiers_constant(self) -> None:
        assert VALID_TIERS == ("disabled", "shadow", "log", "warn", "block")


class TestAdminMatchesDaemonDanglingRefFix:
    """admin and daemon views share one resolve step, so a dangling
    escalate/rewrite outcome maps to allow the same way in both."""

    def test_dangling_ref_mapping_matches_daemon_load(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        rule = dict(
            DECIDE_RULE,
            name="guard",
            on={"violation": {"action": "rewrite", "rule": "missing"}},
        )
        _write_rule(home / ".vaudeville" / "rules", "guard.yaml", rule)
        monkeypatch.setenv("HOME", str(home))

        daemon_rules = load_rules_layered(None).by_name()
        admin_rule = next(r for r, _source in list_rules_with_source(None) if r.name == "guard")

        guard = daemon_rules["guard"]
        assert isinstance(guard, DecideRule)
        assert isinstance(admin_rule, DecideRule)
        assert guard.on["violation"].action == "allow"
        assert admin_rule.on["violation"].action == "allow"
