"""Tests for vaudeville.core — protocol, client, truncation."""

from __future__ import annotations

import os
import tempfile
import time

from vaudeville.core.client import VaudevilleClient
from vaudeville.core.truncation import back_truncate


# --- Fail-open path ---


class TestFailOpen:
    def test_client_fast_path_missing_socket(self) -> None:
        """Socket-exists guard returns None in <100ms, not the 1s connect timeout."""
        client = VaudevilleClient()
        client._socket_path = "/tmp/nonexistent-fast-path-test.sock"
        start = time.monotonic()
        result = client.hook({"op": "hook"})
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 0.1, f"Expected <100ms, got {elapsed:.3f}s (socket timeout?)"

    def test_client_fast_path_with_real_socket_file(self) -> None:
        """When socket file exists but nothing listens, client gets ConnectionRefused."""
        with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
            fake_socket = f.name
        try:
            client = VaudevilleClient()
            client._socket_path = fake_socket
            result = client.hook({"op": "hook"})
            # File exists but not a real socket — should fail with connection error
            assert result is None
        finally:
            os.unlink(fake_socket)

    def test_client_returns_none_for_missing_socket(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client = VaudevilleClient()
            client._socket_path = os.path.join(td, "nonexistent.sock")
            result = client.hook({"op": "hook"})
            assert result is None


# --- Back-truncation ---


class TestBackTruncate:
    def test_short_text_unchanged(self) -> None:
        assert back_truncate("hello") == "hello"

    def test_truncates_to_last_chars(self) -> None:
        # max_tokens=1 → max_chars=4; keep last 4 chars
        result = back_truncate("abcdefgh", max_tokens=1)
        assert result == "efgh"

    def test_exact_boundary_unchanged(self) -> None:
        text = "x" * (1500 * 4)
        assert back_truncate(text) == text

    def test_over_boundary_keeps_tail(self) -> None:
        tail = "violation here"
        text = "a" * 20000 + tail
        result = back_truncate(text)
        assert result.endswith(tail)
        assert len(result) == 3000 * 4

    def test_empty_string(self) -> None:
        assert back_truncate("") == ""
