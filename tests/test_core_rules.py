"""Tests for vaudeville.core — Rule context resolution and layered rule loading."""

from __future__ import annotations

import os
import tempfile
from typing import Any

from vaudeville.core.rules import (
    Rule,
    load_rules_layered,
    parse_rule,
    rules_search_path,
)


class TestRuleContext:
    def test_format_prompt_with_context(self) -> None:
        rule = Rule(
            name="test",
            event="Stop",
            prompt="Text: {text}\nContext: {context}",
            context=[],
            action="block",
            message="{reason}",
        )
        result = rule.format_prompt("hello", "world")
        assert "Text: hello" in result
        assert "Context: world" in result

    def test_resolve_context_field(self) -> None:
        rule = Rule(
            name="test",
            event="Stop",
            prompt="{text}",
            context=[{"field": "last_assistant_message"}],
            action="block",
            message="{reason}",
        )
        ctx = rule.resolve_context({"last_assistant_message": "hello"})
        assert ctx == "hello"

    def test_resolve_context_file(self) -> None:
        rule = Rule(
            name="test",
            event="Stop",
            prompt="{text}",
            context=[{"file": "content.txt"}],
            action="block",
            message="{reason}",
        )
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "content.txt"), "w") as f:
                f.write("file content here")
            ctx = rule.resolve_context({}, plugin_root=d)
            assert ctx == "file content here"

    def test_resolve_context_missing_file(self) -> None:
        rule = Rule(
            name="test",
            event="Stop",
            prompt="{text}",
            context=[{"file": "nonexistent.txt"}],
            action="block",
            message="{reason}",
        )
        ctx = rule.resolve_context({}, plugin_root="/tmp")
        assert ctx == ""

    def test_resolve_context_dotted_field_path(self) -> None:
        rule = Rule(
            name="test",
            event="Stop",
            prompt="{text}",
            context=[{"field": "tool_input.body"}],
            action="block",
            message="{reason}",
        )
        ctx = rule.resolve_context({"tool_input": {"body": "nested value"}})
        assert ctx == "nested value"

    def test_resolve_context_dotted_field_missing_key(self) -> None:
        rule = Rule(
            name="test",
            event="Stop",
            prompt="{text}",
            context=[{"field": "tool_input.nonexistent"}],
            action="block",
            message="{reason}",
        )
        ctx = rule.resolve_context({"tool_input": {"body": "value"}})
        assert ctx == ""


class TestParseRule:
    """Tests for parse_rule — validates Rule construction from YAML data."""

    def _minimal_data(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {"name": "test", "prompt": "{text}"}
        base.update(overrides)
        return base

    def test_name_and_prompt_required_fields(self) -> None:
        rule = parse_rule(self._minimal_data())
        assert rule.name == "test"
        assert rule.prompt == "{text}"

    def test_defaults_action_to_block(self) -> None:
        rule = parse_rule(self._minimal_data())
        assert rule.action == "block"

    def test_defaults_threshold_to_zero(self) -> None:
        rule = parse_rule(self._minimal_data())
        assert rule.threshold == 0.0

    def test_custom_action_preserved(self) -> None:
        rule = parse_rule(self._minimal_data(action="warn"))
        assert rule.action == "warn"

    def test_custom_threshold_preserved(self) -> None:
        rule = parse_rule(self._minimal_data(threshold=0.75))
        assert rule.threshold == 0.75

    def test_custom_event_preserved(self) -> None:
        rule = parse_rule(self._minimal_data(event="PreToolUse"))
        assert rule.event == "PreToolUse"

    def test_context_list_preserved(self) -> None:
        ctx = [{"field": "last_assistant_message"}]
        rule = parse_rule(self._minimal_data(context=ctx))
        assert rule.context == ctx

    def test_ignores_unknown_fields(self) -> None:
        rule = parse_rule(self._minimal_data(labels=["spam", "ham"], unknown="value"))
        assert rule.name == "test"
        assert not hasattr(rule, "labels")


class TestRulesSearchPath:
    def test_empty_when_no_dirs_exist(self) -> None:
        """Returns empty list when neither global nor project rules dirs exist."""
        import unittest.mock as mock

        with mock.patch("vaudeville.core.rules.os.path.isdir", return_value=False):
            path = rules_search_path(project_root="/nonexistent/path")
        assert path == []

    def test_project_dir_included_when_exists(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir:
            rules_dir = os.path.join(project_dir, ".vaudeville", "rules")
            os.makedirs(rules_dir)
            path = rules_search_path(project_root=project_dir)
            assert rules_dir in path

    def test_project_dir_last_in_path(self) -> None:
        """Project dir is highest priority — must come last."""
        with tempfile.TemporaryDirectory() as project_dir:
            rules_dir = os.path.join(project_dir, ".vaudeville", "rules")
            os.makedirs(rules_dir)
            path = rules_search_path(project_root=project_dir)
            if len(path) > 1:
                assert path[-1] == rules_dir

    def test_no_project_root_skips_project_dir(self) -> None:
        path = rules_search_path(project_root=None)
        for d in path:
            assert ".vaudeville/rules" not in d or d.startswith(os.path.expanduser("~"))


class TestLoadRulesLayered:
    def test_returns_empty_when_no_dirs_exist(self) -> None:
        import unittest.mock as mock

        with mock.patch("vaudeville.core.rules.os.path.isdir", return_value=False):
            rules = load_rules_layered(project_root="/nonexistent/path")
        assert rules == {}

    def test_loads_rules_from_project_dir(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir:
            rules_dir = os.path.join(project_dir, ".vaudeville", "rules")
            os.makedirs(rules_dir)
            with open(os.path.join(rules_dir, "my-rule.yaml"), "w") as f:
                f.write(
                    "name: my-rule\n"
                    "event: Stop\n"
                    "prompt: 'classify {text}'\n"
                    "action: block\n"
                    "message: '{reason}'\n"
                )
            rules = load_rules_layered(project_root=project_dir)
            assert "my-rule" in rules

    def test_project_dir_overrides_global_dir(self) -> None:
        with (
            tempfile.TemporaryDirectory() as global_dir,
            tempfile.TemporaryDirectory() as project_dir,
        ):
            global_rules_dir = os.path.join(global_dir, "rules")
            project_rules_dir = os.path.join(project_dir, ".vaudeville", "rules")
            os.makedirs(global_rules_dir)
            os.makedirs(project_rules_dir)

            with open(os.path.join(global_rules_dir, "shared-rule.yaml"), "w") as f:
                f.write(
                    "name: shared-rule\nevent: Stop\nprompt: 'global {text}'\n"
                    "action: block\nmessage: '{reason}'\n"
                )
            with open(os.path.join(project_rules_dir, "shared-rule.yaml"), "w") as f:
                f.write(
                    "name: shared-rule\nevent: Stop\nprompt: 'project {text}'\n"
                    "action: warn\nmessage: '{reason}'\n"
                )

            import unittest.mock as mock

            global_path = os.path.join(os.path.expanduser("~"), ".vaudeville", "rules")
            orig_isdir = os.path.isdir

            def fake_isdir(path: str) -> bool:
                if path == global_path:
                    return True
                if path == global_rules_dir:
                    return True
                return orig_isdir(path)

            with mock.patch("vaudeville.core.rules.os.path.isdir", fake_isdir):
                with mock.patch(
                    "vaudeville.core.rules.os.path.expanduser",
                    return_value=global_dir,
                ):
                    rules = load_rules_layered(project_root=project_dir)

            assert rules["shared-rule"].action == "warn"
            assert "project" in rules["shared-rule"].prompt
