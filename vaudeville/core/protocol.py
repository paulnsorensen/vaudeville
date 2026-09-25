"""Hook response protocol.

Stdlib-only — safe to import in hook scripts.
"""

from __future__ import annotations

from typing import TypedDict, TypeGuard


class HookResponse(TypedDict):
    stdout: str
    exit_code: int


GENERIC_ALLOW: HookResponse = {"stdout": "", "exit_code": 0}


def is_hook_response(value: object) -> TypeGuard[HookResponse]:
    """Check that a decoded JSON value matches the HookResponse shape."""
    return (
        isinstance(value, dict)
        and isinstance(value.get("stdout"), str)
        and isinstance(value.get("exit_code"), int)
    )
