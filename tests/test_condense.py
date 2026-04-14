"""Tests for SLM-based semantic content condensing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from conftest import MockBackend
from vaudeville.server.condense import (
    _CHUNK_INPUT_CHARS,
    _MAX_CHUNKS,
    _build_condense_prompt,
    _condense_single,
    _split_into_chunks,
    condense_text,
)
from vaudeville.server import handle_request


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

    def test_under_200_chars_skips(self) -> None:
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


class TestSplitIntoChunks:
    def test_short_text_single_chunk(self) -> None:
        chunks = _split_into_chunks("hello\nworld", 100)
        assert chunks == ["hello\nworld"]

    def test_splits_at_line_boundaries(self) -> None:
        text = "line1\nline2\nline3\nline4"
        chunks = _split_into_chunks(text, 12)
        assert all("\n" not in c or len(c) <= 12 for c in chunks)
        assert len(chunks) > 1

    def test_long_single_line_becomes_own_chunk(self) -> None:
        text = "short\n" + "x" * 200 + "\nshort2"
        chunks = _split_into_chunks(text, 50)
        assert any("x" * 200 in c for c in chunks)

    def test_empty_text(self) -> None:
        chunks = _split_into_chunks("", 100)
        assert chunks == [""]

    def test_preserves_all_content(self) -> None:
        text = "line1\nline2\nline3\nline4\nline5"
        chunks = _split_into_chunks(text, 12)
        reassembled = "\n".join(chunks)
        assert reassembled == text

    def test_exact_boundary(self) -> None:
        # "abcde" is 5 chars + 1 newline = 6 per line accounting
        # Budget of 12 fits both lines; 11 does not (6+6=12)
        text = "abcde\nfghij"
        chunks = _split_into_chunks(text, 12)
        assert len(chunks) == 1
        assert chunks[0] == text


class TestCondenseSingle:
    def test_returns_condensed_output(self) -> None:
        backend = MockBackend()
        backend.classify = lambda prompt, max_tokens=50: "condensed"  # type: ignore[method-assign]
        assert _condense_single("some text", backend, 100) == "condensed"

    def test_fail_open_on_error(self) -> None:
        class ErrorBackend:
            def classify(self, prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG002
                raise RuntimeError("boom")

        assert _condense_single("original", ErrorBackend(), 100) == "original"

    def test_empty_result_returns_original(self) -> None:
        backend = MockBackend()
        backend.classify = lambda prompt, max_tokens=50: "   "  # type: ignore[method-assign]
        assert _condense_single("original", backend, 100) == "original"


class TestCondenseTextChunked:
    def test_large_text_is_chunked(self) -> None:
        backend = MagicMock()
        backend.classify.return_value = "condensed chunk"
        text = "line\n" * (_CHUNK_INPUT_CHARS // 5 * 2)  # ~2 chunks worth
        condense_text(text, backend)
        assert backend.classify.call_count >= 2

    def test_chunk_results_reassembled(self) -> None:
        call_count = 0

        def fake_classify(prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            return f"condensed-{call_count}"

        backend = MockBackend()
        backend.classify = fake_classify  # type: ignore[method-assign]
        text = ("x" * 100 + "\n") * (_CHUNK_INPUT_CHARS // 100 * 2)
        result = condense_text(text, backend)
        assert "condensed-1" in result
        assert "condensed-2" in result

    def test_single_chunk_failure_uses_original(self) -> None:
        call_count = 0

        def failing_second(prompt: str, max_tokens: int = 50) -> str:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("boom")
            return f"ok-{call_count}"

        backend = MockBackend()
        backend.classify = failing_second  # type: ignore[method-assign]
        # Build text with identifiable chunks
        chunk_lines = _CHUNK_INPUT_CHARS // 10
        text = ("aaaaaaaaa\n" * chunk_lines) + ("bbbbbbbbb\n" * chunk_lines)
        result = condense_text(text, backend)
        assert "ok-1" in result
        # Second chunk should be original text (fail-open)
        assert "bbbbbbbbb" in result

    def test_chunks_beyond_max_pass_through(self) -> None:
        backend = MagicMock()
        backend.classify.return_value = "condensed"
        chunk_lines = _CHUNK_INPUT_CHARS // 10
        # Create enough text for _MAX_CHUNKS + 1 chunks
        text = ""
        for i in range(_MAX_CHUNKS + 1):
            text += (f"chunk{i}----\n") * chunk_lines
        result = condense_text(text, backend)
        assert backend.classify.call_count == _MAX_CHUNKS
        # Last chunk should pass through uncondensed
        assert f"chunk{_MAX_CHUNKS}" in result

    def test_under_chunk_threshold_uses_single_pass(self) -> None:
        backend = MagicMock()
        backend.classify.return_value = "condensed"
        text = "x" * 300  # Well under _CHUNK_INPUT_CHARS
        condense_text(text, backend)
        backend.classify.assert_called_once()


class TestClientCondense:
    def test_condense_returns_original_on_no_socket(self) -> None:
        from vaudeville.core.client import VaudevilleClient

        client = VaudevilleClient()
        with patch.object(client, "_socket_path", "/nonexistent/path.sock"):
            result = client.condense("hello world")
            assert result == "hello world"
