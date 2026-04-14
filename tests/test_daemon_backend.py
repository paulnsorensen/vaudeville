"""Tests for vaudeville/server/daemon_backend.py."""

from __future__ import annotations

import json
import math
import os
import socket
import tempfile
import threading
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from vaudeville.server.daemon_backend import (
    DaemonBackend,
    _confidence_to_logprobs,
    _recv_response,
    daemon_is_alive,
)


class FakeServer:
    """Minimal Unix socket server for testing DaemonBackend."""

    def __init__(self, socket_path: str, response: dict[str, object]) -> None:
        self._socket_path = socket_path
        self._response = response
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self._socket_path)
        self._server.listen(1)
        self._server.settimeout(5.0)
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        assert self._server is not None
        try:
            conn, _ = self._server.accept()
            with conn:
                conn.settimeout(5.0)
                data = bytearray()
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data.extend(chunk)
                reply = json.dumps(self._response).encode() + b"\n"
                conn.sendall(reply)
        except (OSError, socket.timeout):
            pass

    def stop(self) -> None:
        if self._server:
            self._server.close()
        if self._thread:
            self._thread.join(timeout=2)


@pytest.fixture
def socket_dir() -> Iterator[str]:
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def fake_daemon(
    socket_dir: str,
) -> Iterator[tuple[str, dict[str, Any]]]:
    path = os.path.join(socket_dir, "test.sock")
    response: dict[str, Any] = {
        "verdict": "violation",
        "reason": "hedging",
        "confidence": 0.85,
    }
    server = FakeServer(path, response)
    server.start()
    yield path, response
    server.stop()


class TestDaemonIsAlive:
    def test_alive_with_listening_socket(
        self, fake_daemon: tuple[str, dict[str, Any]]
    ) -> None:
        path, _ = fake_daemon
        assert daemon_is_alive(path) is True

    def test_dead_when_no_socket_file(self, socket_dir: str) -> None:
        path = os.path.join(socket_dir, "nonexistent.sock")
        assert daemon_is_alive(path) is False

    def test_dead_when_socket_file_exists_but_not_listening(
        self, socket_dir: str
    ) -> None:
        path = os.path.join(socket_dir, "stale.sock")
        open(path, "w").close()
        assert daemon_is_alive(path) is False

    def test_dead_on_connection_refused(self, socket_dir: str) -> None:
        path = os.path.join(socket_dir, "dead.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.close()
        assert daemon_is_alive(path) is False


class TestDaemonBackendClassify:
    def test_classify_returns_verdict_text(
        self, fake_daemon: tuple[str, dict[str, Any]]
    ) -> None:
        path, _ = fake_daemon
        backend = DaemonBackend(socket_path=path)
        result = backend.classify("test prompt")
        assert "VERDICT: violation" in result
        assert "REASON: hedging" in result

    def test_classify_with_logprobs(
        self, fake_daemon: tuple[str, dict[str, Any]]
    ) -> None:
        path, _ = fake_daemon
        backend = DaemonBackend(socket_path=path)
        result = backend.classify_with_logprobs("test prompt")
        assert "VERDICT: violation" in result.text
        assert "violation" in result.logprobs
        assert "clean" in result.logprobs
        assert result.logprobs["violation"] > result.logprobs["clean"]

    def test_raises_on_dead_socket(self, socket_dir: str) -> None:
        path = os.path.join(socket_dir, "dead.sock")
        backend = DaemonBackend(socket_path=path)
        with pytest.raises(OSError):
            backend.classify("test")


class TestRecvResponse:
    def test_parses_newline_terminated_json(self) -> None:
        r, w = socket.socketpair()
        try:
            w.sendall(json.dumps({"verdict": "clean"}).encode() + b"\n")
            result = _recv_response(r)
            assert result["verdict"] == "clean"
        finally:
            r.close()
            w.close()

    def test_handles_chunked_response(self) -> None:
        r, w = socket.socketpair()
        try:
            msg = json.dumps({"verdict": "violation", "reason": "x" * 1000}).encode()
            w.sendall(msg + b"\n")
            result = _recv_response(r)
            assert result["verdict"] == "violation"
        finally:
            r.close()
            w.close()


class TestConfidenceToLogprobs:
    def test_violation_high_confidence(self) -> None:
        lp = _confidence_to_logprobs("violation", 0.9)
        assert lp["violation"] > lp["clean"]
        assert abs(math.exp(lp["violation"]) - 0.9) < 0.01

    def test_clean_high_confidence(self) -> None:
        lp = _confidence_to_logprobs("clean", 0.95)
        assert lp["clean"] > lp["violation"]
        assert abs(math.exp(lp["clean"]) - 0.95) < 0.01

    def test_clamps_extreme_values(self) -> None:
        lp_low = _confidence_to_logprobs("violation", 0.0)
        assert math.isfinite(lp_low["violation"])
        lp_high = _confidence_to_logprobs("violation", 1.0)
        assert math.isfinite(lp_high["violation"])

    def test_roundtrip_with_compute_confidence(self) -> None:
        from vaudeville.core.protocol import compute_confidence

        lp = _confidence_to_logprobs("violation", 0.85)
        recovered = compute_confidence(lp, "violation")
        assert abs(recovered - 0.85) < 0.02


class TestBuildBackendDaemonPreference:
    def test_uses_daemon_when_alive(self) -> None:
        import argparse

        from vaudeville.eval import _build_backend

        args = argparse.Namespace(model="test-model", no_daemon=False)
        with (
            patch(
                "vaudeville.server.daemon_backend.daemon_is_alive", return_value=True
            ),
            patch("vaudeville.server.daemon_backend.DaemonBackend") as mock_cls,
        ):
            sentinel = object()
            mock_cls.return_value = sentinel
            result = _build_backend(args)
        mock_cls.assert_called_once()
        assert result is sentinel

    def test_falls_back_to_mlx_when_no_daemon(self) -> None:
        import argparse

        from vaudeville.eval import _build_backend

        args = argparse.Namespace(model="test-model", no_daemon=False)
        mock_mlx = object()
        with (
            patch(
                "vaudeville.server.daemon_backend.daemon_is_alive", return_value=False
            ),
            patch("vaudeville.server.MLXBackend", return_value=mock_mlx),
        ):
            result = _build_backend(args)
        assert result is mock_mlx

    def test_skips_daemon_with_no_daemon_flag(self) -> None:
        import argparse

        from vaudeville.eval import _build_backend

        args = argparse.Namespace(model="test-model", no_daemon=True)
        mock_mlx = object()
        with patch("vaudeville.server.MLXBackend", return_value=mock_mlx):
            result = _build_backend(args)
        assert result is mock_mlx


class TestNoDaemonCliFlag:
    def test_parser_accepts_no_daemon(self) -> None:
        from vaudeville.eval import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--no-daemon"])
        assert args.no_daemon is True

    def test_parser_default_is_false(self) -> None:
        from vaudeville.eval import _build_parser

        parser = _build_parser()
        args = parser.parse_args([])
        assert args.no_daemon is False
