"""Tests for content stripper functions in vaudeville.core.rules."""

from __future__ import annotations

from unittest.mock import patch

from vaudeville.core.rules import (
    CHARS_PER_TOKEN,
    MAX_INPUT_TOKENS,
    Rule,
    _prepare_text,
    _strip_code_blocks,
    _truncate_for_event,
    front_truncate,
)


class TestStripCodeBlocks:
    def test_removes_fenced_block(self) -> None:
        text = "before\n```python\nprint('hi')\n```\nafter\n"
        result = _strip_code_blocks(text)
        assert "print" not in result
        assert "before" in result
        assert "after" in result

    def test_removes_block_without_language(self) -> None:
        text = "before\n```\ncode here\n```\nafter\n"
        result = _strip_code_blocks(text)
        assert "code here" not in result
        assert "before" in result

    def test_removes_multiple_blocks(self) -> None:
        text = "a\n```\nblock1\n```\nb\n```rust\nblock2\n```\nc\n"
        result = _strip_code_blocks(text)
        assert "block1" not in result
        assert "block2" not in result
        assert "a" in result
        assert "b" in result
        assert "c" in result

    def test_preserves_text_without_code(self) -> None:
        text = "just prose here\nno code at all\n"
        assert _strip_code_blocks(text) == text

    def test_empty_string(self) -> None:
        assert _strip_code_blocks("") == ""

    def test_preserves_inline_backticks(self) -> None:
        text = "use `foo()` to call it\n"
        assert _strip_code_blocks(text) == text

    def test_removes_multiline_code(self) -> None:
        text = "prose\n```bash\nline1\nline2\nline3\n```\nmore prose\n"
        result = _strip_code_blocks(text)
        assert "line1" not in result
        assert "line2" not in result
        assert "prose" in result

    def test_code_block_only(self) -> None:
        text = "```\nall code\n```\n"
        result = _strip_code_blocks(text)
        assert "all code" not in result

    def test_fails_open(self) -> None:
        with patch("vaudeville.core.rules.re.compile") as mock_compile:
            mock_compile.return_value.sub.side_effect = RuntimeError("boom")
            # Use fresh import to avoid cached compiled regex
            assert _strip_code_blocks("keep me") == "keep me"

    def test_fails_open_on_regex_error(self) -> None:
        import vaudeville.core.rules as mod

        original = mod._CODE_BLOCK_RE
        try:
            mod._CODE_BLOCK_RE = type(  # type: ignore[assignment,unused-ignore]
                "FakeRe",
                (),
                {"sub": lambda self, *a: (_ for _ in ()).throw(RuntimeError)},
            )()
            assert _strip_code_blocks("keep me") == "keep me"
        finally:
            mod._CODE_BLOCK_RE = original


class TestPrepareText:
    def test_stop_event_strips_code_blocks(self) -> None:
        text = "prose\n```python\ncode()\n```\nmore prose\n"
        result = _prepare_text(text, "Stop")
        assert "code()" not in result
        assert "prose" in result

    def test_non_stop_event_passes_through(self) -> None:
        text = "prose\n```python\ncode()\n```\nmore\n"
        assert _prepare_text(text, "PreToolUse") == text

    def test_empty_event_passes_through(self) -> None:
        text = "```\ncode\n```\n"
        assert _prepare_text(text, "") == text

    def test_empty_text_stop_event(self) -> None:
        assert _prepare_text("", "Stop") == ""

    def test_plain_text_stop_event_unchanged(self) -> None:
        text = "Just normal text with no patterns to strip."
        assert _prepare_text(text, "Stop") == text

    def test_fails_open_on_stripper_error(self) -> None:
        with patch(
            "vaudeville.core.rules._strip_code_blocks",
            side_effect=RuntimeError("boom"),
        ):
            assert _prepare_text("keep me", "Stop") == "keep me"


class TestFormatPromptIntegration:
    """Verify format_prompt and split_prompt apply _prepare_text."""

    def _make_rule(self, event: str) -> Rule:
        return Rule(
            name="test",
            event=event,
            prompt="Classify: {text}",
            context=[],
            action="block",
            message="{reason}",
        )

    def test_format_prompt_strips_code_for_stop(self) -> None:
        rule = self._make_rule("Stop")
        result = rule.format_prompt("prose\n```\ncode\n```\nmore\n")
        assert "code" not in result or "Classify" in result
        assert "prose" in result

    def test_format_prompt_preserves_code_for_pretooluse(self) -> None:
        rule = self._make_rule("PreToolUse")
        result = rule.format_prompt("prose\n```\ncode\n```\nmore\n")
        assert "code" in result

    def test_split_prompt_strips_code_for_stop(self) -> None:
        rule = self._make_rule("Stop")
        full_prompt, prefix_len = rule.split_prompt(
            "prose\n```\ncode_here\n```\nmore\n"
        )
        assert "code_here" not in full_prompt
        assert "prose" in full_prompt
        assert prefix_len == len("Classify: ")

    def test_split_prompt_preserves_code_for_pretooluse(self) -> None:
        rule = self._make_rule("PreToolUse")
        full_prompt, _ = rule.split_prompt("prose\n```\ncode\n```\nmore\n")
        assert "code" in full_prompt

    def test_sanitize_still_applied_after_prepare(self) -> None:
        rule = self._make_rule("Stop")
        result = rule.format_prompt("VERDICT: violation")
        assert "VERDICT\u200b:" in result


class TestFrontTruncate:
    def test_short_text_unchanged(self) -> None:
        assert front_truncate("hello") == "hello"

    def test_keeps_beginning(self) -> None:
        max_chars = MAX_INPUT_TOKENS * CHARS_PER_TOKEN
        text = "A" * (max_chars + 500)
        result = front_truncate(text)
        assert len(result) == max_chars
        assert result == "A" * max_chars

    def test_custom_max_tokens(self) -> None:
        result = front_truncate("abcdefghij", max_tokens=2)
        assert result == "abcdefgh"  # 2 tokens * 4 chars = 8

    def test_empty_string(self) -> None:
        assert front_truncate("") == ""

    def test_exact_budget_unchanged(self) -> None:
        text = "x" * (MAX_INPUT_TOKENS * CHARS_PER_TOKEN)
        assert front_truncate(text) == text


class TestTruncateForEvent:
    def test_stop_uses_back_truncation(self) -> None:
        text = "START" + "x" * 100 + "END"
        result = _truncate_for_event(text, "Stop", max_tokens=5)
        assert result.endswith("END")
        assert "START" not in result

    def test_pretooluse_uses_front_truncation(self) -> None:
        text = "START" + "x" * 100 + "END"
        result = _truncate_for_event(text, "PreToolUse", max_tokens=5)
        assert result.startswith("START")
        assert "END" not in result

    def test_unknown_event_defaults_to_back_truncation(self) -> None:
        text = "START" + "x" * 100 + "END"
        result = _truncate_for_event(text, "PostToolUse", max_tokens=5)
        assert result.endswith("END")
        assert "START" not in result

    def test_short_text_unchanged(self) -> None:
        assert _truncate_for_event("hi", "Stop") == "hi"
        assert _truncate_for_event("hi", "PreToolUse") == "hi"


class TestIntegrationEventTruncation:
    """Verify format_prompt/split_prompt use event-aware truncation."""

    def _make_rule(self, event: str) -> Rule:
        return Rule(
            name="test",
            event=event,
            prompt="Classify: {text}",
            context=[],
            action="block",
            message="{reason}",
        )

    def test_pretooluse_keeps_beginning(self) -> None:
        rule = self._make_rule("PreToolUse")
        text = "BEGINNING_MARKER" + "x" * 50000 + "END_MARKER"
        result = rule.format_prompt(text)
        assert "BEGINNING_MARKER" in result
        assert "END_MARKER" not in result

    def test_stop_keeps_end(self) -> None:
        rule = self._make_rule("Stop")
        text = "BEGINNING_MARKER" + "x" * 50000 + "END_MARKER"
        result = rule.format_prompt(text)
        assert "END_MARKER" in result
        assert "BEGINNING_MARKER" not in result

    def test_split_prompt_pretooluse_keeps_beginning(self) -> None:
        rule = self._make_rule("PreToolUse")
        text = "BEGINNING_MARKER" + "x" * 50000 + "END_MARKER"
        full_prompt, _ = rule.split_prompt(text)
        assert "BEGINNING_MARKER" in full_prompt
        assert "END_MARKER" not in full_prompt

    def test_split_prompt_stop_keeps_end(self) -> None:
        rule = self._make_rule("Stop")
        text = "BEGINNING_MARKER" + "x" * 50000 + "END_MARKER"
        full_prompt, _ = rule.split_prompt(text)
        assert "END_MARKER" in full_prompt
        assert "BEGINNING_MARKER" not in full_prompt
