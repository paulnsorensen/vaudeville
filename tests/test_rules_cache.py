"""Tests for the load_layered cache, keyed by project root and mtimes (AC-24)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from vaudeville.rules.cache import cache_size, clear_cache, load_layered

DECIDE_RULE: dict[str, Any] = {
    "type": "decide",
    "name": "git-gate",
    "event": "Stop",
    "prompt": "classify",
    "outcomes": ["violation", "clean"],
    "on": {"violation": "block"},
}


@pytest.fixture(autouse=True)
def _isolated_layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "empty-plugin-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    clear_cache()


def _write_rule(directory: Path, filename: str, data: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(yaml.dump(data))
    return path


class TestCacheInvalidation:
    def test_file_edited_misses_cache(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        rules_dir = project / ".vaudeville" / "rules"
        _write_rule(rules_dir, "gate.yaml", dict(DECIDE_RULE, tier="shadow"))

        first = load_layered(str(project))
        assert first.by_name()["git-gate"].tier == "shadow"

        time.sleep(0.01)
        _write_rule(rules_dir, "gate.yaml", dict(DECIDE_RULE, tier="block"))
        os.utime(rules_dir / "gate.yaml", None)

        second = load_layered(str(project))
        assert second.by_name()["git-gate"].tier == "block"

    def test_file_added_misses_cache(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        rules_dir = project / ".vaudeville" / "rules"
        _write_rule(rules_dir, "gate.yaml", DECIDE_RULE)

        first = load_layered(str(project))
        assert set(first.by_name()) == {"git-gate"}

        _write_rule(rules_dir, "other.yaml", dict(DECIDE_RULE, name="other-rule"))

        second = load_layered(str(project))
        assert set(second.by_name()) == {"git-gate", "other-rule"}

    def test_file_removed_misses_cache(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        rules_dir = project / ".vaudeville" / "rules"
        _write_rule(rules_dir, "gate.yaml", DECIDE_RULE)
        _write_rule(rules_dir, "other.yaml", dict(DECIDE_RULE, name="other-rule"))

        first = load_layered(str(project))
        assert set(first.by_name()) == {"git-gate", "other-rule"}

        (rules_dir / "other.yaml").unlink()

        second = load_layered(str(project))
        assert set(second.by_name()) == {"git-gate"}

    def test_second_project_root_misses_cache(self, tmp_path: Path) -> None:
        project_a = tmp_path / "project-a"
        project_b = tmp_path / "project-b"
        _write_rule(
            project_a / ".vaudeville" / "rules",
            "gate.yaml",
            dict(DECIDE_RULE, tier="shadow"),
        )
        _write_rule(
            project_b / ".vaudeville" / "rules",
            "gate.yaml",
            dict(DECIDE_RULE, tier="block"),
        )

        result_a = load_layered(str(project_a))
        result_b = load_layered(str(project_b))

        assert result_a.by_name()["git-gate"].tier == "shadow"
        assert result_b.by_name()["git-gate"].tier == "block"

    def test_unchanged_tree_hits_cache(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        _write_rule(project / ".vaudeville" / "rules", "gate.yaml", DECIDE_RULE)

        first = load_layered(str(project))
        second = load_layered(str(project))
        assert first is second

    def test_edited_file_evicts_stale_entry_instead_of_accumulating(
        self, tmp_path: Path
    ) -> None:
        project = tmp_path / "project"
        rules_dir = project / ".vaudeville" / "rules"
        _write_rule(rules_dir, "gate.yaml", dict(DECIDE_RULE, tier="shadow"))
        load_layered(str(project))
        assert cache_size() == 1

        time.sleep(0.01)
        _write_rule(rules_dir, "gate.yaml", dict(DECIDE_RULE, tier="block"))
        os.utime(rules_dir / "gate.yaml", None)
        load_layered(str(project))

        assert cache_size() == 1

    def test_fingerprint_skips_unreadable_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "project"
        rules_dir = project / ".vaudeville" / "rules"
        _write_rule(rules_dir, "gate.yaml", DECIDE_RULE)
        real_listdir = os.listdir

        def _raising_listdir(path: str) -> list[str]:
            if path == str(rules_dir):
                raise OSError("permission denied")
            return real_listdir(path)

        monkeypatch.setattr(os, "listdir", _raising_listdir)

        result = load_layered(str(project))
        assert result.by_name() == {}

    def test_fingerprint_skips_non_yaml_files(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        rules_dir = project / ".vaudeville" / "rules"
        _write_rule(rules_dir, "gate.yaml", DECIDE_RULE)
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "README.md").write_text("not a rule")

        result = load_layered(str(project))
        assert set(result.by_name()) == {"git-gate"}

    def test_fingerprint_skips_file_that_disappears_before_stat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "project"
        rules_dir = project / ".vaudeville" / "rules"
        _write_rule(rules_dir, "gate.yaml", DECIDE_RULE)
        real_stat = os.stat
        gate_path = str(rules_dir / "gate.yaml")

        def _raising_stat(
            path: object, *args: object, **kwargs: object
        ) -> os.stat_result:
            if path == gate_path:
                raise OSError("vanished")
            return real_stat(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "stat", _raising_stat)

        result = load_layered(str(project))
        assert set(result.by_name()) == {"git-gate"}
