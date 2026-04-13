"""Unix socket client for the vaudeville daemon.

Stdlib-only — safe to import in hook scripts.
"""

from __future__ import annotations

import json
import logging
import os
import socket

from .paths import SOCKET_PATH
from .protocol import ClassifyRequest, ClassifyResponse

CONNECT_TIMEOUT = 1.0  # Localhost socket connect is sub-ms; 1s is generous
READ_TIMEOUT = 8.0  # p95=2346ms observed; 8s fits inside all CC hook budgets
RECV_CHUNK = 4096

logger = logging.getLogger(__name__)


class VaudevilleClient:
    def __init__(self) -> None:
        self._socket_path = SOCKET_PATH

    def classify(
        self,
        prompt: str,
        rule: str = "",
        prefix_len: int = 0,
    ) -> ClassifyResponse | None:
        """Send a classify request and return the verdict.

        Returns None if the daemon is unavailable (fail-open semantics).
        """
        request = ClassifyRequest(prompt=prompt, rule=rule, prefix_len=prefix_len)
        try:
            return self._send(request)
        except Exception as exc:
            logger.warning("[vaudeville] classify failed: %s", exc)
            return None

    def _send(self, request: ClassifyRequest) -> ClassifyResponse:
        if not os.path.exists(self._socket_path):
            raise FileNotFoundError(self._socket_path)

        payload = json.dumps(request.to_json_dict()).encode() + b"\n"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect(self._socket_path)
            sock.settimeout(READ_TIMEOUT)
            sock.sendall(payload)

            data = b""
            while True:
                chunk = sock.recv(RECV_CHUNK)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

        response = json.loads(data.decode().strip())
        return ClassifyResponse(
            verdict=response.get("verdict", "clean"),
            reason=response.get("reason", ""),
            confidence=float(response.get("confidence", 1.0)),
        )
