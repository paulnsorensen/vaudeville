"""Tests for the load_layered cache, keyed by project root and mtimes (AC-24)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from vaudeville.rules import cache as cache_module
from vaudeville.rules.cache import (
    cache_size,
    clear_cache,
    load_layered,
    project_root_for,
)

DECIDE_RULE: dict[str, Any] = {
    "type": "decide",
    "name": "git-gate",
    "event": "Stop",
    "prompt": "classify",
    "outcomes": ["violation", "clean"],
    "on": {"violation": "block"},
}


@pytest.fixture(autouse=True)
def _isolated_layers() -> None:
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

    def test_edited_file_evicts_stale_entry_instead_of_accumulating(self, tmp_path: Path) -> None:
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
        real_iterdir = Path.iterdir

        def _raising_iterdir(self: Path) -> Any:
            if self == rules_dir:
                raise OSError("permission denied")
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _raising_iterdir)

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

        def _raising_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
            if path == gate_path:
                raise OSError("vanished")
            return real_stat(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "stat", _raising_stat)

        result = load_layered(str(project))
        assert set(result.by_name()) == {"git-gate"}


class TestCacheBound:
    """F32: the cache holds at most `_MAX_ROOTS` project roots."""

    def test_distinct_roots_stay_bounded(self, tmp_path: Path) -> None:
        for i in range(cache_module._MAX_ROOTS + 8):
            load_layered(str(tmp_path / f"root-{i}"))

        assert cache_size() == cache_module._MAX_ROOTS

    def test_least_recently_used_root_evicted_first(self, tmp_path: Path) -> None:
        roots = [str(tmp_path / f"root-{i}") for i in range(cache_module._MAX_ROOTS)]
        for root in roots:
            load_layered(root)
        load_layered(roots[0])

        load_layered(str(tmp_path / "one-more"))

        assert os.path.realpath(roots[0]) in cache_module._cache
        assert os.path.realpath(roots[1]) not in cache_module._cache

    def test_symlinked_root_shares_one_entry(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        _write_rule(project / ".vaudeville" / "rules", "gate.yaml", DECIDE_RULE)
        link = tmp_path / "link"
        link.symlink_to(project)

        load_layered(str(project))
        load_layered(str(link))

        assert cache_size() == 1


class TestProjectRootFor:
    """F12: resolve the project root by walking up to the nearest `.git`."""

    def test_subdirectory_resolves_to_git_root(self, tmp_path: Path) -> None:
        (tmp_path / "repo" / ".git").mkdir(parents=True)
        subdir = tmp_path / "repo" / "a" / "b"
        subdir.mkdir(parents=True)

        assert project_root_for(str(subdir)) == str(tmp_path / "repo")

    def test_git_file_marks_a_worktree_root(self, tmp_path: Path) -> None:
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / ".git").write_text("gitdir: /elsewhere\n")
        (tmp_path / "wt" / "src").mkdir()

        assert project_root_for(str(tmp_path / "wt" / "src")) == str(tmp_path / "wt")

    def test_no_git_falls_back_to_cwd(self, tmp_path: Path) -> None:
        cwd = tmp_path / "plain"
        cwd.mkdir()

        assert project_root_for(str(cwd)) == str(cwd)
