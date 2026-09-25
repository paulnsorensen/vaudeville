"""Unix socket client for the vaudeville daemon.

Stdlib-only — safe to import in hook scripts.
"""

from __future__ import annotations

import json
import logging
import os
import socket

from .paths import SOCKET_PATH
from .protocol import HookResponse, is_hook_response

CONNECT_TIMEOUT = 1.0  # Localhost socket connect is sub-ms; 1s is generous
READ_TIMEOUT = 8.0  # p95=2346ms observed; 8s fits inside all CC hook budgets
RECV_CHUNK = 4096

logger = logging.getLogger(__name__)


class VaudevilleClient:
    def __init__(self) -> None:
        self._socket_path = SOCKET_PATH

    def hook(self, request: dict[str, object]) -> HookResponse | None:
        """Send a hook request and return the response.

        Returns None if the daemon is unavailable or the response is
        malformed (fail-open semantics).
        """
        try:
            return self._send(request)
        except Exception as exc:
            logger.warning("[vaudeville] hook failed: %s", exc)
            return None

    def _send(self, request: dict[str, object]) -> HookResponse | None:
        if not os.path.exists(self._socket_path):
            raise FileNotFoundError(self._socket_path)

        payload = json.dumps(request).encode() + b"\n"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect(self._socket_path)
            sock.settimeout(READ_TIMEOUT)
            sock.sendall(payload)

            data = bytearray()
            while True:
                scan_from = len(data)
                chunk = sock.recv(RECV_CHUNK)
                if not chunk:
                    break
                data.extend(chunk)
                if data.find(b"\n", scan_from) >= 0:
                    break

        response = json.loads(bytes(data).decode().strip())
        if not is_hook_response(response):
            return None
        return response
