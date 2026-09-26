"""Harness adapter seam: normalize raw hook JSON, render decided actions.

Imports vaudeville.rules (for Action) and the standard library and pydantic
only. Each concrete adapter lives in its own module; `get_adapter` looks one
up by name so the pipeline stays harness-agnostic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel

from vaudeville.rules import Action


class HookEvent(BaseModel):
    """A hook request normalized to the shape rules and effects operate on."""

    harness: str
    event: str
    tool_name: str | None = None
    tool_input: dict[str, object] | None = None
    cwd: str
    raw: dict[str, object]
    text: str = ""


class RenderResult(TypedDict):
    """The hook stdout/exit-code payload plus this render's own downgrades."""

    stdout: str
    exit_code: int
    downgrades: list[dict[str, str]]


class Outcome(BaseModel):
    """The action the pipeline decided, ready for a harness to render."""

    action: Action
    message: str
    rule: str
    event: str
    updated_input: dict[str, object] | None = None
    context: str | None = None


@runtime_checkable
class Adapter(Protocol):
    """The public seam every harness implements."""

    def normalize(self, raw: Mapping[str, object]) -> HookEvent: ...

    def render(self, outcome: Outcome) -> RenderResult: ...

    def render_allow(self) -> RenderResult: ...


def _build_registry() -> dict[str, Adapter]:
    from vaudeville.server.harness.claude_code import ClaudeCodeAdapter
    from vaudeville.server.harness.codex import CodexAdapter

    return {"claude-code": ClaudeCodeAdapter(), "codex": CodexAdapter()}


_REGISTRY: dict[str, Adapter] = _build_registry()


def get_adapter(name: str) -> Adapter | None:
    """Look up a registered adapter by harness name, or None if unknown."""
    return _REGISTRY.get(name)


__all__ = ["Adapter", "HookEvent", "Outcome", "RenderResult", "get_adapter"]
