"""Daemon-backed inference for eval and tune harnesses.

Wraps the Unix socket client to conform to InferenceBackend,
enabling eval/tune to reuse a warm daemon instead of loading
the model in-process.
"""

from __future__ import annotations

import json
import logging
import math
import os
import socket

from ..core.paths import SOCKET_PATH
from ..core.protocol import ClassifyRequest, ClassifyResult

CONNECT_TIMEOUT = 2.0
READ_TIMEOUT = 30.0
RECV_CHUNK = 4096

logger = logging.getLogger(__name__)


def daemon_is_alive(socket_path: str = SOCKET_PATH) -> bool:
    """Check if the daemon socket exists and accepts connections."""
    if not os.path.exists(socket_path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect(socket_path)
            return True
    except (OSError, socket.timeout):
        return False


def _recv_response(sock: socket.socket) -> dict[str, object]:
    data = bytearray()
    newline_index = -1
    while True:
        scan_from = len(data)
        chunk = sock.recv(RECV_CHUNK)
        if not chunk:
            break
        data.extend(chunk)
        newline_index = data.find(b"\n", scan_from)
        if newline_index >= 0:
            break
    message = bytes(data[:newline_index]) if newline_index >= 0 else bytes(data)
    result: dict[str, object] = json.loads(message.decode().strip())
    return result


class DaemonBackend:
    """InferenceBackend that forwards requests to a warm daemon."""

    def __init__(self, socket_path: str = SOCKET_PATH) -> None:
        self._socket_path = socket_path

    def classify(self, prompt: str, max_tokens: int = 50) -> str:
        response = self._send_classify(prompt)
        verdict = response.get("verdict", "clean")
        reason = response.get("reason", "")
        return f"VERDICT: {verdict}\nREASON: {reason}"

    def classify_with_logprobs(
        self,
        prompt: str,
        max_tokens: int = 50,
    ) -> ClassifyResult:
        response = self._send_classify(prompt)
        verdict = response.get("verdict", "clean")
        reason = response.get("reason", "")
        text = f"VERDICT: {verdict}\nREASON: {reason}"
        confidence = float(str(response.get("confidence", 0.0)))
        logprobs = _confidence_to_logprobs(str(verdict), confidence)
        return ClassifyResult(text=text, logprobs=logprobs)

    def _send_classify(self, prompt: str) -> dict[str, object]:
        # log_event=False keeps eval/tune classifications out of events.jsonl
        # so `vaudeville watch` only shows real hook firings.
        request = ClassifyRequest(prompt=prompt, rule="eval", log_event=False)
        payload = json.dumps(request.to_json_dict()).encode() + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(CONNECT_TIMEOUT)
                sock.connect(self._socket_path)
                sock.settimeout(READ_TIMEOUT)
                sock.sendall(payload)
                return _recv_response(sock)
        except (OSError, socket.timeout, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Daemon classify failed via {self._socket_path}: {exc}"
            ) from exc


def _confidence_to_logprobs(verdict: str, confidence: float) -> dict[str, float]:
    """Reconstruct approximate logprobs from daemon confidence."""
    confidence = max(0.01, min(0.99, confidence))
    other = 1.0 - confidence
    if verdict == "violation":
        return {
            "violation": math.log(confidence),
            "clean": math.log(other),
        }
    return {
        "clean": math.log(confidence),
        "violation": math.log(other),
    }
