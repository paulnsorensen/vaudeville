"""Tests for the harness adapter registry seam (AC-7 support, AC-15 feed)."""

from __future__ import annotations

from vaudeville.server.harness import get_adapter
from vaudeville.server.harness.claude_code import ClaudeCodeAdapter


class TestGetAdapter:
    def test_known_harness_returns_adapter(self) -> None:
        adapter = get_adapter("claude-code")
        assert isinstance(adapter, ClaudeCodeAdapter)

    def test_unknown_harness_returns_none(self) -> None:
        assert get_adapter("nope") is None
