"""Process-local verification obligations for the experimental Claude Code gate."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from vaudeville.rules.cache import project_root_for
from vaudeville.server.harness import HookEvent
from vaudeville.server.user_config import PrototypeVerificationConfig

_MAX_SCOPES = 256
_MAX_HISTORY = 512


@dataclass
class _Scope:
    generation: int = 0
    cleared: int = 0
    attempts: OrderedDict[str, tuple[int, str]] = field(default_factory=OrderedDict)
    completed: OrderedDict[str, None] = field(default_factory=OrderedDict)

    def remember(self, identity: str) -> bool:
        if identity in self.completed:
            return False
        self.completed[identity] = None
        if len(self.completed) > _MAX_HISTORY:
            self.completed.popitem(last=False)
        return True


class ObligationTracker:
    """Observe hooks in received order under a short state lock."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._scopes: OrderedDict[tuple[str, str], _Scope] = OrderedDict()

    def observe(self, event: HookEvent, config: PrototypeVerificationConfig | None) -> bool:
        """Return whether this Stop has an uncleared edit obligation."""
        if event.harness != "claude-code":
            return False
        session = event.raw.get("session_id")
        if not isinstance(session, str) or not session or "agent_id" in event.raw:
            return False
        with self._lock:
            if event.event == "SessionEnd":
                for key in tuple(self._scopes):
                    if key[0] == session:
                        del self._scopes[key]
                return False
            if config is None or not event.cwd:
                return False
            root = Path(project_root_for(event.cwd)).resolve()
            key = (session, str(root))
            state = self._scopes.get(key)
            if state is None:
                state = _Scope()
                self._scopes[key] = state
                if len(self._scopes) > _MAX_SCOPES:
                    self._scopes.popitem(last=False)
            self._scopes.move_to_end(key)
            identity = event.raw.get("tool_use_id")
            tool_input = event.tool_input
            if event.event == "PostToolUse" and event.tool_name in {"Edit", "Write"}:
                path = tool_input.get("file_path") if tool_input else None
                response = event.raw.get("tool_response")
                if (
                    isinstance(identity, str)
                    and identity
                    and isinstance(path, str)
                    and Path(path).is_absolute()
                    and (not isinstance(response, Mapping) or response.get("success") is not False)
                    and (not isinstance(response, Mapping) or "error" not in response)
                    and Path(path).resolve().is_relative_to(root)
                    and state.remember(identity)
                ):
                    state.generation += 1
            elif event.tool_name == "Bash" and isinstance(identity, str) and identity:
                command = tool_input.get("command") if tool_input else None
                if event.event == "PreToolUse":
                    if (
                        isinstance(command, str)
                        and command in config.commands
                        and Path(event.cwd).resolve() == root
                        and identity not in state.attempts
                        and identity not in state.completed
                        and event.raw.get("run_in_background") is not True
                        and (tool_input is None or tool_input.get("run_in_background") is not True)
                    ):
                        state.attempts[identity] = (state.generation, command)
                        if len(state.attempts) > _MAX_HISTORY:
                            state.attempts.popitem(last=False)
                elif (
                    event.event in {"PostToolUse", "PostToolUseFailure"}
                    and identity in state.attempts
                ):
                    generation, started_command = state.attempts.pop(identity)
                    fresh = state.remember(identity)
                    if (
                        fresh
                        and event.event == "PostToolUse"
                        and command == started_command
                        and command in config.commands
                        and Path(event.cwd).resolve() == root
                        and event.raw.get("run_in_background") is not True
                        and (tool_input is None or tool_input.get("run_in_background") is not True)
                        and self._bash_succeeded(event)
                        and generation == state.generation
                    ):
                        state.cleared = generation
            return event.event == "Stop" and state.generation > state.cleared

    @staticmethod
    def _bash_succeeded(event: HookEvent) -> bool:
        response = event.raw.get("tool_response")
        if not isinstance(response, Mapping):
            return False
        if not isinstance(response.get("stdout"), str) or not isinstance(
            response.get("stderr"), str
        ):
            return False
        if response.get("interrupted") is not False:
            return False
        if response.get("success") is False or "error" in response:
            return False
        if response.get("run_in_background") is True or response.get("background") is True:
            return False
        exit_code = response.get("exit_code", 0)
        return type(exit_code) is int and exit_code == 0
