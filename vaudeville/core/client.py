"""Unix socket client for the vaudeville daemon.

Stdlib-only — safe to import in hook scripts.
"""

from __future__ import annotations

import json
import logging
import os
import socket

from .protocol import ClassifyRequest, ClassifyResponse

SOCKET_TEMPLATE = "/tmp/vaudeville-{session_id}.sock"
CONNECT_TIMEOUT = 1.0  # Localhost socket connect is sub-ms; 1s is generous
READ_TIMEOUT = 3.0  # Inference takes ~1-2s; 3s is sufficient
RECV_CHUNK = 4096

logger = logging.getLogger(__name__)


class VaudevilleClient:
    def __init__(self, session_id: str) -> None:
        self._socket_path = SOCKET_TEMPLATE.format(session_id=session_id)

    def classify(
        self, rule: str, input_data: dict[str, object]
    ) -> ClassifyResponse | None:
        """Send a classify request and return the verdict.

        Returns None if the daemon is unavailable (fail-open semantics).
        """
        request = ClassifyRequest(rule=rule, input=input_data)
        try:
            return self._send(request)
        except Exception as exc:
            logger.warning("[vaudeville] classify failed (%s): %s", rule, exc)
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
            action=response.get("action", "block"),
        )
