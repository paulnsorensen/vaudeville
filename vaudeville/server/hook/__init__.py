"""Hook decide pipeline: the daemon-independent seam for `op: hook`.

Public interface: `handle_hook_request(request) -> {stdout, exit_code}`.
"""

from __future__ import annotations

from .pipeline import handle_hook_request

__all__ = ["handle_hook_request"]
