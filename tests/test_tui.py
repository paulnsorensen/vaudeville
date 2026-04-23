"""Tests for vaudeville.server.tui shared UI primitives."""

from __future__ import annotations

from rich import box
from rich.table import Table

from vaudeville.server.tui import latency_text, styled_table


class TestStyledTable:
    def test_returns_table(self) -> None:
        result = styled_table("My Title")
        assert isinstance(result, Table)

    def test_title(self) -> None:
        result = styled_table("My Title")
        assert result.title == "My Title"

    def test_title_style(self) -> None:
        result = styled_table("T")
        assert result.title_style == "bold cyan"

    def test_header_style(self) -> None:
        result = styled_table("T")
        assert result.header_style == "bold magenta"

    def test_caption_style(self) -> None:
        result = styled_table("T", caption="some caption")
        assert result.caption_style == "dim"

    def test_caption_set(self) -> None:
        result = styled_table("T", caption="my cap")
        assert result.caption == "my cap"

    def test_caption_none(self) -> None:
        result = styled_table("T")
        assert result.caption is None

    def test_expand(self) -> None:
        result = styled_table("T")
        assert result.expand is True

    def test_show_lines_false(self) -> None:
        result = styled_table("T")
        assert result.show_lines is False

    def test_box_simple_heavy(self) -> None:
        result = styled_table("T")
        assert result.box is box.SIMPLE_HEAVY


class TestLatencyText:
    def test_at_or_below_100_is_green(self) -> None:
        text = latency_text(100.0)
        assert text.style == "green"

    def test_below_100_is_green(self) -> None:
        text = latency_text(50.0)
        assert text.style == "green"

    def test_just_above_100_is_yellow(self) -> None:
        text = latency_text(101.0)
        assert text.style == "yellow"

    def test_at_500_is_yellow(self) -> None:
        text = latency_text(500.0)
        assert text.style == "yellow"

    def test_above_500_is_red(self) -> None:
        text = latency_text(501.0)
        assert text.style == "red"

    def test_very_high_is_red(self) -> None:
        text = latency_text(9999.0)
        assert text.style == "red"

    def test_zero_is_green(self) -> None:
        text = latency_text(0.0)
        assert text.style == "green"

    def test_formatted_value(self) -> None:
        text = latency_text(123.456)
        assert text.plain == "123.5"
