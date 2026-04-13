"""Tests for content stripper functions in vaudeville.core.rules."""

from __future__ import annotations

from unittest.mock import patch

from vaudeville.core.rules import (
    CHARS_PER_TOKEN,
    MAX_INPUT_TOKENS,
    Rule,
    _prepare_text,
    _strip_blockquotes,
    _strip_recaps,
    _strip_self_quotes,
    _truncate_for_event,
    front_truncate,
)


class TestStripBlockquotes:
    def test_removes_single_level_blockquote(self) -> None:
        text = "> quoted line\nnormal line\n"
        assert _strip_blockquotes(text) == "normal line\n"

    def test_removes_nested_blockquotes(self) -> None:
        text = ">> deeply nested\n> single nested\nplain\n"
        assert _strip_blockquotes(text) == "plain\n"

    def test_preserves_text_without_blockquotes(self) -> None:
        text = "no quotes here\njust text\n"
        assert _strip_blockquotes(text) == text

    def test_removes_multiple_blockquote_blocks(self) -> None:
        text = "> first\ntext\n> second\nmore text\n"
        assert _strip_blockquotes(text) == "text\nmore text\n"

    def test_empty_string(self) -> None:
        assert _strip_blockquotes("") == ""

    def test_blockquote_only(self) -> None:
        assert _strip_blockquotes("> only quote\n") == ""

    def test_blockquote_without_trailing_newline(self) -> None:
        result = _strip_blockquotes("> no newline")
        assert result == ""


class TestStripSelfQuotes:
    def test_removes_i_said_pattern(self) -> None:
        text = 'I said: "This is a quoted passage that is long enough to match"'
        assert _strip_self_quotes(text) == ""

    def test_removes_i_wrote_pattern(self) -> None:
        text = 'I wrote: "Another long enough quoted passage here for testing"'
        assert _strip_self_quotes(text) == ""

    def test_removes_i_mentioned_pattern(self) -> None:
        text = 'I mentioned: "A sufficiently long quoted passage for the test"'
        assert _strip_self_quotes(text) == ""

    def test_preserves_short_quotes(self) -> None:
        text = 'I said: "short"'
        assert _strip_self_quotes(text) == text

    def test_preserves_text_without_self_quotes(self) -> None:
        text = "Regular text without any self-quoting patterns."
        assert _strip_self_quotes(text) == text

    def test_removes_only_self_quote_preserves_surrounding(self) -> None:
        text = (
            'Before. I said: "This is a long enough passage to be stripped out" After.'
        )
        result = _strip_self_quotes(text)
        assert "Before." in result
        assert "After." in result
        assert "long enough passage" not in result

    def test_empty_string(self) -> None:
        assert _strip_self_quotes("") == ""

    def test_multiline_quoted_content(self) -> None:
        text = 'I wrote: "This is a multi-line\nquoted passage that spans lines"'
        result = _strip_self_quotes(text)
        assert "multi-line" not in result


class TestStripRecaps:
    def test_removes_let_me_summarize_paragraph(self) -> None:
        text = "Normal text.\n\nLet me summarize what we discussed.\nPoint one.\nPoint two.\n\nMore text.\n"
        result = _strip_recaps(text)
        assert "Normal text." in result
        assert "More text." in result
        assert "summarize" not in result

    def test_removes_to_recap_paragraph(self) -> None:
        text = "Before.\n\nTo recap, here's what happened.\nDetail A.\nDetail B.\n\nAfter.\n"
        result = _strip_recaps(text)
        assert "Before." in result
        assert "After." in result
        assert "recap" not in result

    def test_removes_in_summary_paragraph(self) -> None:
        text = "Start.\n\nIn summary, the key points are:\nFirst point.\nSecond point.\n\nEnd.\n"
        result = _strip_recaps(text)
        assert "Start." in result
        assert "End." in result
        assert "summary" not in result.lower()

    def test_case_insensitive(self) -> None:
        text = "Before.\n\nTO RECAP the situation.\nDetails here.\n\nAfter.\n"
        result = _strip_recaps(text)
        assert "RECAP" not in result

    def test_preserves_text_without_recaps(self) -> None:
        text = "Normal paragraph.\n\nAnother paragraph.\n"
        assert _strip_recaps(text) == text

    def test_empty_string(self) -> None:
        assert _strip_recaps("") == ""

    def test_recap_at_end_without_trailing_blank_line(self) -> None:
        text = "Before.\n\nTo recap everything.\nDone."
        result = _strip_recaps(text)
        # No trailing paragraph break — regex won't match, text preserved (fail-open)
        assert "Before." in result


class TestFailOpen:
    """All strippers must return original text on internal errors."""

    def test_strip_blockquotes_fails_open(self) -> None:
        with patch("vaudeville.core.rules.re.sub", side_effect=RuntimeError("boom")):
            assert _strip_blockquotes("keep me") == "keep me"

    def test_strip_self_quotes_fails_open(self) -> None:
        import vaudeville.core.rules as mod

        original = mod._SELF_QUOTE_RE
        try:
            mod._SELF_QUOTE_RE = type(  # type: ignore[assignment,unused-ignore]
                "FakeRe",
                (),
                {"sub": lambda self, *a: (_ for _ in ()).throw(RuntimeError)},
            )()
            assert _strip_self_quotes("keep me") == "keep me"
        finally:
            mod._SELF_QUOTE_RE = original

    def test_strip_recaps_fails_open(self) -> None:
        import vaudeville.core.rules as mod

        original = mod._RECAP_RE
        try:
            mod._RECAP_RE = type(  # type: ignore[assignment,unused-ignore]
                "FakeRe",
                (),
                {"sub": lambda self, *a: (_ for _ in ()).throw(RuntimeError)},
            )()
            assert _strip_recaps("keep me") == "keep me"
        finally:
            mod._RECAP_RE = original


class TestPrepareText:
    """Tests for _prepare_text orchestration."""

    def test_stop_event_strips_blockquotes(self) -> None:
        text = "> quoted\nreal content\n"
        result = _prepare_text(text, "Stop")
        assert "> quoted" not in result
        assert "real content" in result

    def test_stop_event_strips_self_quotes(self) -> None:
        text = 'I said: "This is a long enough passage to be stripped out" rest'
        result = _prepare_text(text, "Stop")
        assert "long enough passage" not in result
        assert "rest" in result

    def test_stop_event_strips_recaps(self) -> None:
        text = "Before.\n\nTo recap the discussion.\nDetail.\n\nAfter.\n"
        result = _prepare_text(text, "Stop")
        assert "recap" not in result
        assert "Before." in result
        assert "After." in result

    def test_stop_event_chains_all_strippers(self) -> None:
        text = (
            "> blockquote line\n"
            'I wrote: "A sufficiently long self-quote for testing"\n'
            "Normal content here.\n\n"
            "Let me summarize the key points.\nPoint one.\n\n"
            "Final paragraph.\n"
        )
        result = _prepare_text(text, "Stop")
        assert "> blockquote" not in result
        assert "self-quote" not in result
        assert "summarize" not in result
        assert "Normal content here." in result
        assert "Final paragraph." in result

    def test_non_stop_event_passes_through(self) -> None:
        text = '> blockquote\nI said: "long enough quote to be stripped normally"\n'
        assert _prepare_text(text, "PreToolUse") == text

    def test_empty_event_passes_through(self) -> None:
        text = "> keep this\n"
        assert _prepare_text(text, "") == text

    def test_empty_text_stop_event(self) -> None:
        assert _prepare_text("", "Stop") == ""

    def test_plain_text_stop_event_unchanged(self) -> None:
        text = "Just normal text with no patterns to strip."
        assert _prepare_text(text, "Stop") == text

    def test_fails_open_on_stripper_error(self) -> None:
        with patch(
            "vaudeville.core.rules._strip_blockquotes",
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

    def test_format_prompt_strips_blockquotes_for_stop(self) -> None:
        rule = self._make_rule("Stop")
        result = rule.format_prompt("> quoted\nreal content\n")
        assert "> quoted" not in result
        assert "real content" in result

    def test_format_prompt_preserves_blockquotes_for_pretooluse(self) -> None:
        rule = self._make_rule("PreToolUse")
        result = rule.format_prompt("> quoted\nreal content\n")
        assert "> quoted" in result

    def test_split_prompt_strips_blockquotes_for_stop(self) -> None:
        rule = self._make_rule("Stop")
        full_prompt, prefix_len = rule.split_prompt("> quoted\nreal content\n")
        assert "> quoted" not in full_prompt
        assert "real content" in full_prompt
        assert prefix_len == len("Classify: ")

    def test_split_prompt_preserves_blockquotes_for_pretooluse(self) -> None:
        rule = self._make_rule("PreToolUse")
        full_prompt, _ = rule.split_prompt("> quoted\nreal content\n")
        assert "> quoted" in full_prompt

    def test_format_prompt_strips_self_quotes_for_stop(self) -> None:
        rule = self._make_rule("Stop")
        text = 'I said: "This is a long enough passage to be stripped out" rest'
        result = rule.format_prompt(text)
        assert "long enough passage" not in result
        assert "rest" in result

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
        # Back-truncate keeps the end
        assert result.endswith("END")
        assert "START" not in result

    def test_pretooluse_uses_front_truncation(self) -> None:
        text = "START" + "x" * 100 + "END"
        result = _truncate_for_event(text, "PreToolUse", max_tokens=5)
        # Front-truncate keeps the beginning
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
