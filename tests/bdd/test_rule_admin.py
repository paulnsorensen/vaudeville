from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
import yaml
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("rule_admin.feature")


@dataclass(frozen=True)
class RuleCLI:
    home: Path
    project: Path
    cwd: Path

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(Path(sys.executable).parent / "vaudeville"), *args],
            cwd=self.cwd,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(self.home),
                "CLAUDE_PLUGIN_ROOT": str(self.home / "no-plugin"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "NO_COLOR": "1",
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def show(self, name: str) -> dict[str, object]:
        result = self.run("show", name, "--json")
        assert result.returncode == 0, result.stderr
        return cast(dict[str, object], json.loads(result.stdout))

    def list(self) -> list[dict[str, object]]:
        result = self.run("list", "--json")
        assert result.returncode == 0, result.stderr
        return cast(list[dict[str, object]], json.loads(result.stdout))


@pytest.fixture
def rule_cli(tmp_path: Path) -> RuleCLI:
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    cwd = project / "nested"
    cwd.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--quiet", str(project)],
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        check=True,
        capture_output=True,
        timeout=10,
    )
    return RuleCLI(home, project, cwd)


def write_rule(root: Path, name: str, tier: str) -> Path:
    rules = root / ".vaudeville" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    path = rules / f"{name}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "type": "decide",
                "name": name,
                "event": "Stop",
                "tier": tier,
                "prompt": "Classify this text: {text}",
                "outcomes": ["violation", "clean"],
                "on": {"violation": "warn"},
            }
        )
    )
    return path


@given(parsers.parse('a user rule named "{name}" at tier "{tier}"'))
def user_rule(rule_cli: RuleCLI, name: str, tier: str) -> None:
    write_rule(rule_cli.home, name, tier)


@given(
    parsers.parse('a project rule named "{name}" at tier "{tier}"'),
    target_fixture="project_snapshot",
)
def project_rule(rule_cli: RuleCLI, name: str, tier: str) -> tuple[Path, bytes]:
    path = write_rule(rule_cli.project, name, tier)
    return path, path.read_bytes()


@given("the current rule state is recorded", target_fixture="rule_snapshot")
def record_state(rule_cli: RuleCLI) -> tuple[list[dict[str, object]], dict[str, object]]:
    rules = rule_cli.list()
    assert rules == [
        {
            "name": "review",
            "type": "decide",
            "tier": "warn",
            "event": "Stop",
            "source": str(rule_cli.home / ".vaudeville" / "rules"),
        }
    ]
    return rules, rule_cli.show("review")


@when(parsers.parse('I {action} the rule named "{name}"'))
def change_rule(rule_cli: RuleCLI, action: str, name: str) -> None:
    result = rule_cli.run(action, name)
    assert result.returncode == 0, result.stderr


@when(
    parsers.parse('I request deletion of the rule named "{name}" without confirmation'),
    target_fixture="delete_result",
)
def request_delete(rule_cli: RuleCLI, name: str) -> subprocess.CompletedProcess[str]:
    return rule_cli.run("delete", name)


@then(parsers.parse('the rule named "{name}" has tier "{tier}" in a later invocation'))
def persisted_tier(rule_cli: RuleCLI, name: str, tier: str) -> None:
    rule = rule_cli.show(name)
    assert rule["tier"] == tier
    assert rule["name"] == name
    assert rule["path"] == str(rule_cli.home / ".vaudeville" / "rules" / f"{name}.yaml")
    assert rule["prompt"] == "Classify this text: {text}"
    assert rule_cli.list() == [
        {
            "name": name,
            "type": "decide",
            "tier": tier,
            "event": "Stop",
            "source": str(rule_cli.home / ".vaudeville" / "rules"),
        }
    ]


@then("deletion fails with a confirmation error")
def confirmation_error(delete_result: subprocess.CompletedProcess[str]) -> None:
    assert delete_result.returncode == 1
    assert delete_result.stderr.splitlines()[-1] == (
        "Rule 'review': pass --yes for non-interactive delete."
    )
    assert delete_result.stdout == ""


@then("the current rule state is unchanged")
def unchanged_state(
    rule_cli: RuleCLI,
    rule_snapshot: tuple[list[dict[str, object]], dict[str, object]],
) -> None:
    rules, rule = rule_snapshot
    assert rule_cli.list() == rules
    assert rule_cli.show("review") == rule


@then("the project rule remains unchanged")
def unchanged_project_rule(project_snapshot: tuple[Path, bytes]) -> None:
    path, content = project_snapshot
    assert path.read_bytes() == content
