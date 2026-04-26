"""Tests for rule management CLI commands and core rule-file helpers."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
import yaml

from vaudeville.cli_rules import (
    cmd_delete,
    cmd_demote,
    cmd_disable,
    cmd_enable,
    cmd_list,
    cmd_path,
    cmd_promote,
    cmd_show,
    cmd_validate,
    cmd_completion,
    dispatch_rule_command,
)
from vaudeville.core.rules import (
    locate_rule_file,
    locate_all_rule_files,
    set_tier,
    list_rules_with_source,
    load_rule_file,
)


def _write_rule(
    rules_dir: Path,
    name: str,
    tier: str = "shadow",
    event: str = "Stop",
    examples: list[dict[str, str]] | None = None,
) -> Path:
    rules_dir.mkdir(parents=True, exist_ok=True)
    path = rules_dir / f"{name}.yaml"
    data: dict[str, object] = {
        "name": name,
        "event": event,
        "tier": tier,
        "threshold": 0.5,
        "action": "warn",
        "message": "{reason}",
        "prompt": "Is this a violation? {{ examples }}\n{text}",
        "context": [{"field": "last_assistant_message"}],
        "labels": ["violation", "clean"],
        "test_cases": [
            {"text": "bad text", "label": "violation"},
            {"text": "good text", "label": "clean"},
        ],
    }
    if examples:
        data["examples"] = examples
    path.write_text(yaml.dump(data))
    return path


def _home_rules(tmp_path: Path) -> Path:
    return tmp_path / ".vaudeville" / "rules"


def _proj_rules(tmp_path: Path) -> Path:
    return tmp_path / "proj" / ".vaudeville" / "rules"


# ---------------------------------------------------------------------------
# Core helpers: locate_rule_file
# ---------------------------------------------------------------------------


class TestLocateRuleFile:
    def test_finds_in_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "test-rule")
        path = locate_rule_file("test-rule")
        assert path.name == "test-rule.yaml"

    def test_finds_in_project_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "test-rule", tier="shadow")
        _write_rule(_proj_rules(tmp_path), "test-rule", tier="warn")
        path = locate_rule_file("test-rule", str(tmp_path / "proj"))
        assert "proj" in str(path)

    def test_falls_back_to_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "test-rule")
        path = locate_rule_file("test-rule", str(tmp_path / "proj"))
        assert path.parent.parent.parent == tmp_path

    def test_raises_when_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(FileNotFoundError, match="test-rule"):
            locate_rule_file("test-rule")


class TestLocateAllRuleFiles:
    def test_returns_empty_when_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        assert locate_all_rule_files("test-rule") == []

    def test_returns_single_location(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "test-rule")
        paths = locate_all_rule_files("test-rule")
        assert len(paths) == 1

    def test_returns_both_locations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "test-rule")
        _write_rule(_proj_rules(tmp_path), "test-rule")
        paths = locate_all_rule_files("test-rule", str(tmp_path / "proj"))
        assert len(paths) == 2


class TestSetTier:
    def test_updates_existing_tier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "test-rule", tier="shadow")
        set_tier("test-rule", "warn")
        content = (_home_rules(tmp_path) / "test-rule.yaml").read_text()
        assert "tier: warn" in content
        assert "tier: shadow" not in content

    def test_appends_tier_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        rules_dir = _home_rules(tmp_path)
        rules_dir.mkdir(parents=True)
        p = rules_dir / "no-tier.yaml"
        p.write_text("name: no-tier\nevent: Stop\n")
        set_tier("no-tier", "block")
        assert "tier: block" in p.read_text()

    def test_raises_on_invalid_tier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "test-rule")
        with pytest.raises(ValueError, match="Invalid tier"):
            set_tier("test-rule", "invalid")

    def test_returns_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "test-rule", tier="shadow")
        result = set_tier("test-rule", "warn")
        assert isinstance(result, Path)
        assert result.exists()


class TestListRulesWithSource:
    def test_returns_pairs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "rule-a")
        _write_rule(_home_rules(tmp_path), "rule-b", tier="warn")
        pairs = list_rules_with_source()
        names = {r.name for r, _ in pairs}
        assert "rule-a" in names and "rule-b" in names

    def test_project_overrides_global(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "test-rule", tier="shadow")
        _write_rule(_proj_rules(tmp_path), "test-rule", tier="warn")
        pairs = list_rules_with_source(str(tmp_path / "proj"))
        rule, source = next((r, s) for r, s in pairs if r.name == "test-rule")
        assert rule.tier == "warn"
        assert "proj" in source

    def test_empty_when_no_rules(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        assert list_rules_with_source() == []


class TestLoadRuleFile:
    def test_loads_valid_rule(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / "rules"
        path = _write_rule(rules_dir, "test-rule")
        rule = load_rule_file(path)
        assert rule is not None
        assert rule.name == "test-rule"

    def test_returns_none_for_draft(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir(parents=True)
        p = rules_dir / "draft.yaml"
        p.write_text("name: draft\ndraft: true\nevent: Stop\nprompt: x\n")
        assert load_rule_file(p) is None


# ---------------------------------------------------------------------------
# CLI: cmd_list
# ---------------------------------------------------------------------------


class TestCmdList:
    def _args(self, **kwargs: object) -> Namespace:
        defaults = {
            "tier": None,
            "event": None,
            "json": False,
            "live": False,
            "poll_interval": 0.5,
        }
        defaults.update(kwargs)  # type: ignore[arg-type]
        return Namespace(**defaults)

    def test_human_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "rule-a")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_list(self._args())
        out = capsys.readouterr().out
        assert "rule-a" in out

    def test_json_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "rule-a")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_list(self._args(json=True))
        data = json.loads(capsys.readouterr().out)
        assert data[0]["name"] == "rule-a"

    def test_filter_by_tier(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "rule-shadow", tier="shadow")
        _write_rule(_home_rules(tmp_path), "rule-warn", tier="warn")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_list(self._args(tier="warn"))
        out = capsys.readouterr().out
        assert "rule-warn" in out
        assert "rule-shadow" not in out

    def test_filter_by_event(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "stop-rule", event="Stop")
        _write_rule(_home_rules(tmp_path), "pre-rule", event="PreToolUse")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_list(self._args(event="Stop"))
        out = capsys.readouterr().out
        assert "stop-rule" in out
        assert "pre-rule" not in out

    def test_empty_when_no_rules(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_list(self._args())
        assert "No rules found." in capsys.readouterr().out

    def test_live_output_uses_refresh_loop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "rule-a")
        with (
            patch(
                "vaudeville.cli_rules._find_project_root",
                return_value=str(tmp_path / "proj"),
            ),
            patch("vaudeville.cli_rules._run_list_live") as mock_live,
        ):
            cmd_list(self._args(live=True, poll_interval=0.2))
        mock_live.assert_called_once_with(
            project_root=str(tmp_path / "proj"),
            tier=None,
            event=None,
            poll_interval=0.2,
        )


# ---------------------------------------------------------------------------
# CLI: cmd_show
# ---------------------------------------------------------------------------


class TestCmdShow:
    def test_human_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule", tier="warn")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_show(Namespace(name="my-rule", json=False))
        out = capsys.readouterr().out
        assert "my-rule" in out
        assert "warn" in out

    def test_json_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_show(Namespace(name="my-rule", json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["name"] == "my-rule"
        assert "path" in data

    def test_not_found_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_show(Namespace(name="missing", json=False))

    def test_shows_test_case_counts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_show(Namespace(name="my-rule", json=False))
        out = capsys.readouterr().out
        assert "test_cases:" not in out

    def test_hides_prompt_footer_variants(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        rules_dir = _home_rules(tmp_path)
        rules_dir.mkdir(parents=True, exist_ok=True)
        path = rules_dir / "footer-rule.yaml"
        data = {
            "name": "footer-rule",
            "event": "Stop",
            "tier": "warn",
            "threshold": 0.5,
            "action": "warn",
            "message": "{reason}",
            "prompt": (
                "Header line\n\n"
                "Now classify:\n"
                "{text}\n\n"
                "VERDICT: violation or clean\n"
                "REASON: one sentence\n"
            ),
            "labels": ["violation", "clean"],
        }
        path.write_text(yaml.dump(data))
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_show(Namespace(name="footer-rule", json=False))
        out = capsys.readouterr().out
        assert "Header line" in out
        assert "Now classify" not in out
        assert "VERDICT: violation or clean" not in out


# ---------------------------------------------------------------------------
# CLI: cmd_delete
# ---------------------------------------------------------------------------


class TestCmdDelete:
    def test_delete_with_yes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_delete(Namespace(name="my-rule", yes=True))
        assert not p.exists()
        assert "Deleted" in capsys.readouterr().out

    def test_delete_aborts_on_no(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with patch("builtins.input", return_value="N"):
                with patch("sys.stdin") as mock_stdin:
                    mock_stdin.isatty.return_value = True
                    cmd_delete(Namespace(name="my-rule", yes=False))
        assert p.exists()
        assert "Aborted" in capsys.readouterr().out

    def test_delete_not_found_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_delete(Namespace(name="missing", yes=True))

    def test_non_interactive_without_yes_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                with pytest.raises(SystemExit):
                    cmd_delete(Namespace(name="my-rule", yes=False))


# ---------------------------------------------------------------------------
# CLI: cmd_promote / cmd_demote
# ---------------------------------------------------------------------------


class TestCmdPromote:
    def test_shadow_to_warn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule", tier="shadow")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_promote(Namespace(name="my-rule"))
        assert "tier: log" in p.read_text()
        assert "shadow → log" in capsys.readouterr().out

    def test_log_to_warn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule", tier="log")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_promote(Namespace(name="my-rule"))
        assert "tier: warn" in p.read_text()
        assert "log → warn" in capsys.readouterr().out

    def test_warn_to_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule", tier="warn")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_promote(Namespace(name="my-rule"))
        assert "tier: block" in p.read_text()

    def test_ceiling_is_noop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule", tier="block")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_promote(Namespace(name="my-rule"))
        assert "ceiling" in capsys.readouterr().out
        assert "tier: block" in p.read_text()

    def test_disabled_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule", tier="disabled")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_promote(Namespace(name="my-rule"))


class TestCmdDemote:
    def test_block_to_warn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule", tier="block")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_demote(Namespace(name="my-rule"))
        assert "tier: warn" in p.read_text()
        assert "block → warn" in capsys.readouterr().out

    def test_warn_to_log(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule", tier="warn")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_demote(Namespace(name="my-rule"))
        assert "tier: log" in p.read_text()
        assert "warn → log" in capsys.readouterr().out

    def test_log_to_shadow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule", tier="log")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_demote(Namespace(name="my-rule"))
        assert "tier: shadow" in p.read_text()

    def test_floor_is_noop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule", tier="shadow")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_demote(Namespace(name="my-rule"))
        assert "floor" in capsys.readouterr().out

    def test_disabled_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule", tier="disabled")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_demote(Namespace(name="my-rule"))


# ---------------------------------------------------------------------------
# CLI: cmd_disable / cmd_enable
# ---------------------------------------------------------------------------


class TestCmdDisable:
    def test_sets_tier_disabled_and_saves_sidecar(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule", tier="warn")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_disable(Namespace(name="my-rule"))
        content = p.read_text()
        assert "tier: disabled" in content
        assert "# previous-tier: warn" in content
        assert "was" in capsys.readouterr().out

    def test_already_disabled_is_noop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule", tier="disabled")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_disable(Namespace(name="my-rule"))
        assert "already disabled" in capsys.readouterr().out

    def test_not_found_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_disable(Namespace(name="missing"))


class TestCmdEnable:
    def test_restores_previous_tier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule", tier="warn")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_disable(Namespace(name="my-rule"))
            cmd_enable(Namespace(name="my-rule"))
        content = p.read_text()
        assert "tier: warn" in content
        assert "# previous-tier:" not in content

    def test_defaults_to_shadow_without_sidecar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule", tier="disabled")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_enable(Namespace(name="my-rule"))
        assert "tier: shadow" in p.read_text()

    def test_already_enabled_is_noop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule", tier="warn")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_enable(Namespace(name="my-rule"))
        assert "already enabled" in capsys.readouterr().out

    def test_not_found_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_enable(Namespace(name="missing"))


# ---------------------------------------------------------------------------
# CLI: cmd_path
# ---------------------------------------------------------------------------


class TestCmdPath:
    def test_prints_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_path(Namespace(name="my-rule"))
        out = capsys.readouterr().out.strip()
        assert out.endswith("my-rule.yaml")

    def test_not_found_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_path(Namespace(name="missing"))


# ---------------------------------------------------------------------------
# CLI: cmd_validate
# ---------------------------------------------------------------------------


class TestCmdValidate:
    def test_single_valid_rule(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_validate(Namespace(name="my-rule"))
        assert "OK" in capsys.readouterr().out

    def test_single_rule_not_found_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_validate(Namespace(name="missing"))

    def test_all_rules_valid(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "rule-a")
        _write_rule(_home_rules(tmp_path), "rule-b")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_validate(Namespace(name=None))
        out = capsys.readouterr().out
        assert "OK" in out
        assert "rule-a" in out

    def test_invalid_rule_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        rules_dir = _home_rules(tmp_path)
        rules_dir.mkdir(parents=True)
        bad = rules_dir / "bad.yaml"
        bad.write_text("tier: bad-tier\nname: bad\nevent: Stop\nprompt: x\n")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_validate(Namespace(name=None))


# ---------------------------------------------------------------------------
# CLI: cmd_completion
# ---------------------------------------------------------------------------


class TestCmdCompletion:
    def test_bash(self, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_completion(Namespace(shell="bash"))
        assert "register-python-argcomplete" in capsys.readouterr().out

    def test_zsh(self, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_completion(Namespace(shell="zsh"))
        assert "register-python-argcomplete" in capsys.readouterr().out

    def test_fish(self, capsys: pytest.CaptureFixture[str]) -> None:
        cmd_completion(Namespace(shell="fish"))
        assert "fish" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# dispatch_rule_command
# ---------------------------------------------------------------------------


class TestDispatchRuleCommand:
    def test_dispatches_known_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            handled = dispatch_rule_command(Namespace(command="path", name="my-rule"))
        assert handled is True

    def test_returns_false_for_unknown(self) -> None:
        assert dispatch_rule_command(Namespace(command="unknown")) is False


# ---------------------------------------------------------------------------
# attach_rule_parsers
# ---------------------------------------------------------------------------


class TestAttachRuleParsers:
    def test_registers_all_subcommands(self) -> None:
        import argparse

        from vaudeville.cli_rules import attach_rule_parsers

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        attach_rule_parsers(sub)

        name_cmds = ("show", "delete", "promote", "demote", "enable", "disable", "path")
        for cmd in name_cmds:
            args = parser.parse_args([cmd, "x"])
            assert args.cmd == cmd
        args = parser.parse_args(["list"])
        assert args.cmd == "list"
        args = parser.parse_args(["validate"])
        assert args.cmd == "validate"
        args = parser.parse_args(["completion", "bash"])
        assert args.cmd == "completion"

    def test_list_filters_registered(self) -> None:
        import argparse

        from vaudeville.cli_rules import attach_rule_parsers

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        attach_rule_parsers(sub)
        args = parser.parse_args(
            ["list", "--tier", "warn", "--event", "Stop", "--json"]
        )
        assert args.tier == "warn"
        assert args.event == "Stop"
        assert args.json is True
        assert args.live is False

        parser2 = argparse.ArgumentParser()
        sub2 = parser2.add_subparsers(dest="cmd")
        attach_rule_parsers(sub2)
        args2 = parser2.parse_args(["list", "--live", "--poll-interval", "0.2"])
        assert args2.live is True
        assert args2.json is False
        assert args2.poll_interval == 0.2

    def test_list_json_live_mutually_exclusive(self) -> None:
        import argparse

        from vaudeville.cli_rules import attach_rule_parsers

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        attach_rule_parsers(sub)
        with pytest.raises(SystemExit):
            parser.parse_args(["list", "--json", "--live"])

    def test_completion_choices_registered(self) -> None:
        import argparse

        from vaudeville.cli_rules import attach_rule_parsers

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        attach_rule_parsers(sub)
        args = parser.parse_args(["completion", "bash"])
        assert args.shell == "bash"

    @pytest.mark.parametrize("value", ["-1", "0", "nan", "inf", "-inf"])
    def test_list_invalid_poll_interval_rejected(self, value: str) -> None:
        import argparse

        from vaudeville.cli_rules import attach_rule_parsers

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        attach_rule_parsers(sub)

        with pytest.raises(SystemExit):
            parser.parse_args(["list", "--poll-interval", value])


# ---------------------------------------------------------------------------
# Additional coverage: not-found paths, edge cases
# ---------------------------------------------------------------------------


class TestCoverageEdgeCases:
    def test_run_list_live_updates_until_interrupted(self) -> None:
        from vaudeville.cli_rules import _run_list_live

        class _FakeLive:
            instances: list["_FakeLive"] = []

            def __init__(self, *_: object, **__: object) -> None:
                self.updated: list[object] = []
                _FakeLive.instances.append(self)

            def __enter__(self) -> "_FakeLive":
                return self

            def __exit__(
                self,
                _exc_type: object,
                _exc_val: object,
                _exc_tb: object,
            ) -> Literal[False]:
                return False

            def update(self, table: object) -> None:
                self.updated.append(table)

        with (
            patch("vaudeville.cli_rules._list_rule_pairs", return_value=[]),
            patch(
                "vaudeville.cli_rules._build_list_table",
                side_effect=["initial-table", "updated-table"],
            ),
            patch("rich.live.Live", _FakeLive),
            patch("vaudeville.cli_rules.time.sleep", side_effect=KeyboardInterrupt),
        ):
            _run_list_live(
                project_root="/tmp/proj",
                tier=None,
                event=None,
                poll_interval=0.25,
            )

        assert len(_FakeLive.instances) == 1
        assert _FakeLive.instances[0].updated == ["updated-table"]

    def test_promote_not_found_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch(
            "vaudeville.cli_rules._find_project_root", return_value=str(tmp_path)
        ):
            with pytest.raises(SystemExit):
                cmd_promote(Namespace(name="missing"))

    def test_demote_not_found_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch(
            "vaudeville.cli_rules._find_project_root", return_value=str(tmp_path)
        ):
            with pytest.raises(SystemExit):
                cmd_demote(Namespace(name="missing"))

    def test_show_with_examples(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(
            _home_rules(tmp_path),
            "ex-rule",
            examples=[
                {
                    "id": "1",
                    "input": "bad output",
                    "label": "violation",
                    "reason": "sycophantic",
                }
            ],
        )
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_show(Namespace(name="ex-rule", json=False))
        out = capsys.readouterr().out
        assert "examples (from prompt)" not in out
        assert "bad output" not in out

    def test_show_draft_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        rules_dir = _home_rules(tmp_path)
        rules_dir.mkdir(parents=True)
        p = rules_dir / "draft-rule.yaml"
        p.write_text("name: draft-rule\ndraft: true\nevent: Stop\nprompt: x\n")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_show(Namespace(name="draft-rule", json=False))

    def test_disable_rule_without_tier_field(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        rules_dir = _home_rules(tmp_path)
        rules_dir.mkdir(parents=True)
        p = rules_dir / "no-tier.yaml"
        p.write_text("name: no-tier\nevent: Stop\nprompt: x\n")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_disable(Namespace(name="no-tier"))
        assert "tier: disabled" in p.read_text()
        assert "# previous-tier: shadow" in p.read_text()

    def test_validate_single_invalid_rule(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        rules_dir = _home_rules(tmp_path)
        rules_dir.mkdir(parents=True)
        p = rules_dir / "bad.yaml"
        p.write_text("name: bad\ntier: invalid-tier\nevent: Stop\nprompt: x\n")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with pytest.raises(SystemExit):
                cmd_validate(Namespace(name="bad"))

    def test_validate_all_skips_non_yaml(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        rules_dir = _home_rules(tmp_path)
        rules_dir.mkdir(parents=True)
        (rules_dir / "README.txt").write_text("not a rule")
        _write_rule(rules_dir, "good-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_validate(Namespace(name=None))
        out = capsys.readouterr().out
        assert "good-rule" in out
        assert "README" not in out

    def test_delete_single_confirmed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        p = _write_rule(_home_rules(tmp_path), "my-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with patch("builtins.input", return_value="y"):
                with patch("sys.stdin") as ms:
                    ms.isatty.return_value = True
                    cmd_delete(Namespace(name="my-rule", yes=False))
        assert not p.exists()

    def test_delete_multiple_index_select(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        ph = _write_rule(_home_rules(tmp_path), "dup-rule")
        pp = _write_rule(_proj_rules(tmp_path), "dup-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with patch("builtins.input", return_value="1"):
                with patch("sys.stdin") as ms:
                    ms.isatty.return_value = True
                    cmd_delete(Namespace(name="dup-rule", yes=False))
        assert not ph.exists() or not pp.exists()

    def test_delete_multiple_all(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        ph = _write_rule(_home_rules(tmp_path), "dup-rule")
        pp = _write_rule(_proj_rules(tmp_path), "dup-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with patch("builtins.input", return_value="all"):
                with patch("sys.stdin") as ms:
                    ms.isatty.return_value = True
                    cmd_delete(Namespace(name="dup-rule", yes=False))
        assert not ph.exists()
        assert not pp.exists()

    def test_delete_multiple_invalid_choice(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "dup-rule")
        _write_rule(_proj_rules(tmp_path), "dup-rule")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            with patch("builtins.input", return_value="99"):
                with patch("sys.stdin") as ms:
                    ms.isatty.return_value = True
                    cmd_delete(Namespace(name="dup-rule", yes=False))
        assert "Aborted" in capsys.readouterr().out

    def test_rule_names_completer_exception(self) -> None:
        from vaudeville.cli_rules import _rule_names_completer

        with patch(
            "vaudeville.cli_rules.load_rules_layered", side_effect=Exception("boom")
        ):
            result = _rule_names_completer("my")
        assert result == []

    def test_rule_names_completer_happy_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_rule(_home_rules(tmp_path), "my-rule")
        from vaudeville.cli_rules import _rule_names_completer

        result = _rule_names_completer("my")
        assert "my-rule" in result

    def test_find_project_root_fallback(self) -> None:
        import os

        from vaudeville.cli_rules import _find_project_root

        with patch("vaudeville.core.paths.find_project_root", return_value=None):
            result = _find_project_root()
        assert result == os.getcwd()

    def test_disable_no_trailing_newline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        rules_dir = _home_rules(tmp_path)
        rules_dir.mkdir(parents=True)
        p = rules_dir / "notail.yaml"
        p.write_text("name: notail\nevent: Stop\nprompt: x\ntier: warn")
        with patch(
            "vaudeville.cli_rules._find_project_root",
            return_value=str(tmp_path / "proj"),
        ):
            cmd_disable(Namespace(name="notail"))
        content = p.read_text()
        assert "tier: disabled" in content
        assert "# previous-tier: warn" in content
