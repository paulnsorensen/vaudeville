"""Request handlers for the Vaudeville daemon."""

from __future__ import annotations

import json
import logging

from vaudeville.core.protocol import GENERIC_ALLOW

from .event_log import EventLogger
from .hook import handle_hook_request

logger = logging.getLogger(__name__)


def handle_request(
    data: bytes,
    event_logger: EventLogger | None = None,
) -> bytes:
    """Route a request by op field. Only `op: hook` is served; anything else
    or any error fails open with an allow response.
    """
    try:
        request = json.loads(data.decode().strip())
        op = str(request.get("op", ""))
        if op == "hook":
            response = handle_hook_request(request, event_logger=event_logger)
        else:
            logger.warning("Unknown op %r — allowing", op)
            response = dict(GENERIC_ALLOW)
    except Exception as exc:
        logger.error("Request error: %s — allowing", exc)
        response = dict(GENERIC_ALLOW)
    return json.dumps(response).encode() + b"\n"
