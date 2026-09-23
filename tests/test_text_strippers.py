"""Tests for content stripper functions in vaudeville.core.truncation."""

from __future__ import annotations

from unittest.mock import patch

from vaudeville.core.truncation import (
    CHARS_PER_TOKEN,
    MAX_INPUT_TOKENS,
    _strip_code_blocks,
    front_truncate,
    prepare_text,
    truncate_for_event,
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
        with patch("vaudeville.core.truncation._CODE_BLOCK_RE") as mock_re:
            mock_re.sub.side_effect = RuntimeError("boom")
            # prepare_text wraps _strip_code_blocks; fail-open returns original
            assert prepare_text("keep me", "Stop") == "keep me"


class TestPrepareText:
    def test_stop_event_strips_code_blocks(self) -> None:
        text = "prose\n```python\ncode()\n```\nmore prose\n"
        result = prepare_text(text, "Stop")
        assert "code()" not in result
        assert "prose" in result

    def test_non_stop_event_passes_through(self) -> None:
        text = "prose\n```python\ncode()\n```\nmore\n"
        assert prepare_text(text, "PreToolUse") == text

    def test_empty_event_passes_through(self) -> None:
        text = "```\ncode\n```\n"
        assert prepare_text(text, "") == text

    def test_empty_text_stop_event(self) -> None:
        assert prepare_text("", "Stop") == ""

    def test_plain_text_stop_event_unchanged(self) -> None:
        text = "Just normal text with no patterns to strip."
        assert prepare_text(text, "Stop") == text

    def test_fails_open_on_stripper_error(self) -> None:
        with patch(
            "vaudeville.core.truncation._strip_code_blocks",
            side_effect=RuntimeError("boom"),
        ):
            assert prepare_text("keep me", "Stop") == "keep me"


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
    def test_stop_uses_sandwich_truncation(self) -> None:
        text = "START" + "x" * 200 + "END"
        result = truncate_for_event(text, "Stop", max_tokens=20)
        assert result.endswith("END")
        assert result.startswith("START")
        assert "[...]" in result

    def test_pretooluse_uses_front_truncation(self) -> None:
        text = "START" + "x" * 100 + "END"
        result = truncate_for_event(text, "PreToolUse", max_tokens=5)
        assert result.startswith("START")
        assert "END" not in result

    def test_unknown_event_defaults_to_back_truncation(self) -> None:
        text = "START" + "x" * 100 + "END"
        result = truncate_for_event(text, "PostToolUse", max_tokens=5)
        assert result.endswith("END")
        assert "START" not in result

    def test_short_text_unchanged(self) -> None:
        assert truncate_for_event("hi", "Stop") == "hi"
        assert truncate_for_event("hi", "PreToolUse") == "hi"
