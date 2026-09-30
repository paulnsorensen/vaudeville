"""Tests for `.codex-plugin/*.json` and `.agents/plugins/marketplace.json`."""

from __future__ import annotations

import json
import os
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_JSON = os.path.join(PROJECT_ROOT, ".codex-plugin", "plugin.json")
HOOKS_JSON = os.path.join(PROJECT_ROOT, ".codex-plugin", "hooks.json")
MARKETPLACE_JSON = os.path.join(PROJECT_ROOT, ".agents", "plugins", "marketplace.json")


def _load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return dict(json.load(handle))


class TestPluginManifest:
    def test_parses_and_has_required_fields(self) -> None:
        manifest = _load(PLUGIN_JSON)
        assert manifest["name"] == "vaudeville"
        assert isinstance(manifest["version"], str)
        assert isinstance(manifest["description"], str) and manifest["description"]

    def test_resolves_codex_hooks(self) -> None:
        manifest = _load(PLUGIN_JSON)
        hooks_path = os.path.realpath(os.path.join(PROJECT_ROOT, manifest["hooks"]))
        assert hooks_path == os.path.realpath(HOOKS_JSON)
        hooks = _load(hooks_path)
        commands = [
            hook["command"]
            for entries in hooks["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
            if "runner.py" in hook["command"]
        ]
        assert commands
        assert all("--harness codex" in command for command in commands)


class TestHooksJson:
    def test_parses(self) -> None:
        hooks = _load(HOOKS_JSON)
        assert "hooks" in hooks

    def test_every_command_calls_the_codex_harness(self) -> None:
        hooks = _load(HOOKS_JSON)
        found_runner_command = False
        for entries in hooks["hooks"].values():
            for entry in entries:
                for hook in entry["hooks"]:
                    command = hook["command"]
                    if "runner.py" in command:
                        found_runner_command = True
                        assert "--harness codex" in command, command
        assert found_runner_command

    def test_session_start_also_runs_session_start_sh(self) -> None:
        hooks = _load(HOOKS_JSON)
        commands = [
            hook["command"] for entry in hooks["hooks"]["SessionStart"] for hook in entry["hooks"]
        ]
        assert any("session-start.sh" in command for command in commands)

    def test_covers_the_required_codex_events(self) -> None:
        hooks = _load(HOOKS_JSON)
        required = {
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "PermissionRequest",
            "Stop",
            "SubagentStop",
        }
        assert required <= set(hooks["hooks"])


class TestMarketplaceJson:
    def test_parses_and_has_required_fields(self) -> None:
        marketplace = _load(MARKETPLACE_JSON)
        assert isinstance(marketplace["name"], str) and marketplace["name"]
        plugins = marketplace["plugins"]
        assert isinstance(plugins, list) and plugins
        entry = dict(plugins[0])
        assert entry["name"] == "vaudeville"
        assert entry["category"]
        assert entry["policy"]["installation"] in {
            "NOT_AVAILABLE",
            "AVAILABLE",
            "INSTALLED_BY_DEFAULT",
        }
