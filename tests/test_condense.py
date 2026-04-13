"""Tests for SLM-based semantic content condensing."""

from __future__ import annotations

import json
from unittest.mock import patch

from conftest import MockBackend
from vaudeville.server.condense import (
    _build_condense_prompt,
    condense_text,
)
from vaudeville.server.daemon import handle_request


class TestBuildCondensePrompt:
    def test_injects_text(self) -> None:
        result = _build_condense_prompt("hello world")
        assert "hello world" in result
        assert "{text}" not in result

    def test_prompt_structure(self) -> None:
        result = _build_condense_prompt("sample")
        assert "CONDENSED:" in result
        assert "TEXT:" in result


class TestCondenseText:
    def test_returns_condensed_output(self) -> None:
        backend = MockBackend(verdict="clean", reason="unused")
        backend.classify = lambda prompt, max_tokens=50: "condensed result"  # type: ignore[method-assign]
        result = condense_text("x" * 300, backend)
        assert result == "condensed result"

    def test_short_text_skips_condensing(self) -> None:
        backend = MockBackend()
        result = condense_text("short", backend)
        assert result == "short"
        assert len(backend.calls) == 0

    def test_exactly_200_chars_skips(self) -> None:
        backend = MockBackend()
        text = "a" * 199
        result = condense_text(text, backend)
        assert result == text
        assert len(backend.calls) == 0

    def test_201_chars_runs_condensing(self) -> None:
        backend = MockBackend()
        backend.classify = lambda prompt, max_tokens=50: "out"  # type: ignore[method-assign]
        text = "a" * 201
        assert condense_text(text, backend) == "out"

    def test_fail_open_on_backend_error(self) -> None:
        class ErrorBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                raise RuntimeError("boom")

        original = "x" * 300
        result = condense_text(original, ErrorBackend())
        assert result == original

    def test_fail_open_on_empty_result(self) -> None:
        backend = MockBackend()
        backend.classify = lambda prompt, max_tokens=50: "   "  # type: ignore[method-assign]
        original = "x" * 300
        result = condense_text(original, backend)
        assert result == original

    def test_strips_whitespace_from_result(self) -> None:
        backend = MockBackend()
        backend.classify = lambda prompt, max_tokens=50: "  condensed  "  # type: ignore[method-assign]
        result = condense_text("x" * 300, backend)
        assert result == "condensed"


class TestDaemonCondenseRouting:
    def test_condense_op_returns_text(self) -> None:
        backend = MockBackend()
        backend.classify = lambda prompt, max_tokens=50: "condensed output"  # type: ignore[method-assign]
        data = json.dumps({"op": "condense", "text": "x" * 300}).encode()
        response = json.loads(handle_request(data, backend))
        assert response["text"] == "condensed output"

    def test_condense_op_short_text_passthrough(self) -> None:
        backend = MockBackend()
        data = json.dumps({"op": "condense", "text": "short"}).encode()
        response = json.loads(handle_request(data, backend))
        assert response["text"] == "short"

    def test_classify_op_default(self) -> None:
        backend = MockBackend(verdict="clean", reason="ok")
        data = json.dumps({"prompt": "test"}).encode()
        response = json.loads(handle_request(data, backend))
        assert response["verdict"] == "clean"

    def test_explicit_classify_op(self) -> None:
        backend = MockBackend(verdict="violation", reason="bad")
        data = json.dumps({"op": "classify", "prompt": "test"}).encode()
        response = json.loads(handle_request(data, backend))
        assert response["verdict"] == "violation"

    def test_condense_fail_open_on_error(self) -> None:
        class ErrorBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                raise RuntimeError("boom")

        text = "x" * 300
        data = json.dumps({"op": "condense", "text": text}).encode()
        response = json.loads(handle_request(data, ErrorBackend()))
        assert response["text"] == text


class TestClientCondense:
    def test_condense_returns_original_on_no_socket(self) -> None:
        from vaudeville.core.client import VaudevilleClient

        client = VaudevilleClient()
        with patch.object(client, "_socket_path", "/nonexistent/path.sock"):
            result = client.condense("hello world")
            assert result == "hello world"
