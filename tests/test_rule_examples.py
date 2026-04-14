"""Tests for structured examples and render_prompt in vaudeville.core.rules."""

from __future__ import annotations

import os
import tempfile

from vaudeville.core.rules import (
    Example,
    Rule,
    _format_examples,
    _parse_examples,
    load_rules,
    parse_rule,
    render_prompt,
)


def _rule_with_examples(
    examples: list[Example] | None = None,
    candidates: list[Example] | None = None,
) -> Rule:
    return Rule(
        name="test",
        event="Stop",
        prompt="Classify:\n\n{{ examples }}\n\n{text}",
        context=[],
        action="block",
        message="{reason}",
        examples=examples or [],
        candidates=candidates or [],
    )


class TestExample:
    def test_construction(self) -> None:
        ex = Example(id="ex1", input="hello", label="clean", reason="test")
        assert ex.id == "ex1"
        assert ex.input == "hello"
        assert ex.label == "clean"
        assert ex.reason == "test"


class TestFormatExamples:
    def test_single_example(self) -> None:
        ex = Example(id="ex1", input="some input", label="violation", reason="bad")
        result = _format_examples([ex])
        assert result == "some input\nVERDICT: violation\nREASON: bad"

    def test_multiple_examples_separated_by_blank_line(self) -> None:
        examples = [
            Example(id="ex1", input="input1", label="violation", reason="r1"),
            Example(id="ex2", input="input2", label="clean", reason="r2"),
        ]
        result = _format_examples(examples)
        assert "\n\n" in result
        assert "input1\nVERDICT: violation\nREASON: r1" in result
        assert "input2\nVERDICT: clean\nREASON: r2" in result

    def test_empty_list(self) -> None:
        assert _format_examples([]) == ""


class TestRenderPrompt:
    def test_renders_all_examples_when_no_ids(self) -> None:
        ex = Example(id="ex1", input="test input", label="clean", reason="ok")
        rule = _rule_with_examples(examples=[ex])
        result = render_prompt(rule)
        assert "{{ examples }}" not in result
        assert "test input" in result
        assert "VERDICT: clean" in result

    def test_selects_by_id(self) -> None:
        ex1 = Example(id="ex1", input="first", label="violation", reason="r1")
        ex2 = Example(id="ex2", input="second", label="clean", reason="r2")
        rule = _rule_with_examples(examples=[ex1, ex2])
        result = render_prompt(rule, example_ids=["ex2"])
        assert "second" in result
        assert "first" not in result

    def test_includes_candidates_when_selected(self) -> None:
        ex = Example(id="ex1", input="example", label="clean", reason="r1")
        cand = Example(id="c1", input="candidate", label="violation", reason="r2")
        rule = _rule_with_examples(examples=[ex], candidates=[cand])
        result = render_prompt(rule, example_ids=["c1"])
        assert "candidate" in result
        assert "example" not in result

    def test_no_placeholder_returns_prompt_unchanged(self) -> None:
        rule = Rule(
            name="test",
            event="Stop",
            prompt="Just classify: {text}",
            context=[],
            action="block",
            message="{reason}",
        )
        assert render_prompt(rule) == "Just classify: {text}"

    def test_unknown_ids_skipped(self) -> None:
        ex = Example(id="ex1", input="hello", label="clean", reason="ok")
        rule = _rule_with_examples(examples=[ex])
        result = render_prompt(rule, example_ids=["nonexistent"])
        assert "hello" not in result


class TestParseExamples:
    def test_parses_valid_examples(self) -> None:
        raw = [
            {"id": "ex1", "input": "text", "label": "clean", "reason": "ok"},
        ]
        examples = _parse_examples(raw)
        assert len(examples) == 1
        assert examples[0].id == "ex1"

    def test_skips_non_dict_entries(self) -> None:
        raw = [
            {"id": "ex1", "input": "t", "label": "clean", "reason": "r"},
            "not a dict",
            42,
        ]
        examples = _parse_examples(raw)
        assert len(examples) == 1

    def test_empty_list(self) -> None:
        assert _parse_examples([]) == []


class TestParseRuleWithExamples:
    def test_parses_examples_field(self) -> None:
        data = {
            "name": "test",
            "prompt": "{{ examples }}\n{text}",
            "examples": [
                {"id": "ex1", "input": "i", "label": "violation", "reason": "r"},
            ],
        }
        rule = parse_rule(data)
        assert len(rule.examples) == 1
        assert rule.examples[0].id == "ex1"

    def test_parses_candidates_field(self) -> None:
        data = {
            "name": "test",
            "prompt": "{{ examples }}\n{text}",
            "candidates": [
                {"id": "c1", "input": "i", "label": "clean", "reason": "r"},
            ],
        }
        rule = parse_rule(data)
        assert len(rule.candidates) == 1
        assert rule.candidates[0].id == "c1"

    def test_defaults_empty_when_no_examples(self) -> None:
        rule = parse_rule({"name": "test", "prompt": "{text}"})
        assert rule.examples == []
        assert rule.candidates == []


class TestFormatPromptWithExamples:
    def test_renders_examples_then_substitutes_text(self) -> None:
        ex = Example(id="ex1", input="demo", label="clean", reason="ok")
        rule = _rule_with_examples(examples=[ex])
        result = rule.format_prompt("user input here")
        assert "demo" in result
        assert "user input here" in result
        assert "{{ examples }}" not in result
        assert "{text}" not in result

    def test_split_prompt_renders_examples(self) -> None:
        ex = Example(id="ex1", input="demo", label="clean", reason="ok")
        rule = _rule_with_examples(examples=[ex])
        full, prefix_len = rule.split_prompt("user input")
        assert "demo" in full
        assert full[prefix_len:].startswith("user input")

    def test_consistent_format_and_split(self) -> None:
        ex = Example(id="ex1", input="demo", label="clean", reason="ok")
        rule = _rule_with_examples(examples=[ex])
        formatted = rule.format_prompt("test text")
        full, _ = rule.split_prompt("test text")
        assert formatted == full


class TestLoadMigratedRules:
    def test_load_deferral_detector_examples(self) -> None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rules_dir = os.path.join(project_root, "examples", "rules")
        rules = load_rules(rules_dir)
        rule = rules["deferral-detector"]
        assert len(rule.examples) == 7
        assert rule.examples[0].id == "ex1"
        assert "{{ examples }}" in rule.prompt

    def test_rendered_prompt_has_no_unresolved_placeholders(self) -> None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rules_dir = os.path.join(project_root, "examples", "rules")
        rules = load_rules(rules_dir)
        for name, rule in rules.items():
            rendered = rule.format_prompt("sample text")
            assert "{{ examples }}" not in rendered, f"{name}: unresolved placeholder"

    def test_load_yaml_with_examples_roundtrip(self) -> None:
        """Write a rule YAML with examples, load it, render prompt."""
        yaml_content = (
            "name: test-rule\n"
            "event: Stop\n"
            "prompt: |\n"
            "  {{ examples }}\n"
            "  Classify: {text}\n"
            "examples:\n"
            "  - id: ex1\n"
            "    input: 'Response: hello'\n"
            "    label: clean\n"
            "    reason: greeting\n"
            "action: block\n"
            "message: '{reason}'\n"
        )
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test-rule.yaml"), "w") as f:
                f.write(yaml_content)
            rules = load_rules(d)
            rule = rules["test-rule"]
            assert len(rule.examples) == 1
            rendered = rule.format_prompt("input text")
            assert "Response: hello" in rendered
            assert "VERDICT: clean" in rendered
            assert "input text" in rendered
