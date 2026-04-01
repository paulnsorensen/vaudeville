"""Tests for vaudeville.core — Rule context resolution and layered rule loading."""

from __future__ import annotations

import os
import tempfile

from vaudeville.core.rules import (
    Rule,
    load_rules_layered,
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


class TestRulesSearchPath:
    def test_bundled_rules_always_in_path(self) -> None:
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = rules_search_path(plugin_root)
        assert len(path) >= 1
        assert path[0].endswith("/rules")

    def test_nonexistent_plugin_root_returns_empty(self) -> None:
        path = rules_search_path("/nonexistent/plugin/root")
        for d in path:
            assert "/nonexistent/" not in d


class TestLoadRulesLayered:
    def test_loads_bundled_rules(self) -> None:
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rules = load_rules_layered(plugin_root)
        assert "violation-detector" in rules

    def test_project_override_wins(self) -> None:
        plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        with tempfile.TemporaryDirectory() as project_dir:
            rules_dir = os.path.join(project_dir, ".vaudeville", "rules")
            os.makedirs(rules_dir)
            with open(os.path.join(rules_dir, "violation-detector.yaml"), "w") as f:
                f.write(
                    "name: violation-detector\n"
                    "event: Stop\n"
                    "prompt: 'override {text}'\n"
                    "labels: [violation, clean]\n"
                    "action: warn\n"
                    "message: '{reason}'\n"
                )

            rules = load_rules_layered(plugin_root, project_root=project_dir)
            assert rules["violation-detector"].action == "warn"
            assert "override" in rules["violation-detector"].prompt
