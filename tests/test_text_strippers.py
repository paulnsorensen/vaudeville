"""Tests for content stripper functions in vaudeville.core.rules."""

from __future__ import annotations

from unittest.mock import patch

from vaudeville.core.rules import (
    _strip_blockquotes,
    _strip_recaps,
    _strip_self_quotes,
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
