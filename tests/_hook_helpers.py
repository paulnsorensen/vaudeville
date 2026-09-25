"""Shared fixtures for the hook pipeline, tier ceiling, and precedence tests."""

from __future__ import annotations

import functools
from typing import Any

import pytest
from pathlib import Path
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vaudeville.server.agents import decide
from vaudeville.server.hook import pipeline as pipeline_module
from vaudeville.server.user_config import ProviderConfig, UserConfig

CONFIG = UserConfig(
    default_model="fake:model",
    providers={"fake": ProviderConfig(key_env="FAKE_KEY")},
)


class Recorder:
    def __init__(self, output: str) -> None:
        self.call_count = 0

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            del messages, info
            self.call_count += 1
            return ModelResponse(parts=[TextPart(output)])

        self.model = FunctionModel(respond)


def decide_fn(output: str) -> tuple[Any, Recorder]:
    recorder = Recorder(output)
    fn = functools.partial(decide, model_override=recorder.model)
    return fn, recorder


def write_rule(tmp_path: Path, name: str, body: str) -> None:
    rules_dir = tmp_path / ".vaudeville" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / f"{name}.yaml").write_text(body)


def make_request(
    tmp_path: Path,
    tool_name: str = "Write",
    tool_input: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "op": "hook",
        "harness": "claude-code",
        "event": "PreToolUse",
        "cwd": str(tmp_path),
        "payload": {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input
            if tool_input is not None
            else {"command": "echo hi"},
            "cwd": str(tmp_path),
        },
    }


class RunRecorder:
    """Records `run_named_command` calls in place of starting a process."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(
        self, name: str, config: UserConfig, event_json: str, *, timeout: float
    ) -> bool:
        del config, event_json, timeout
        self.calls.append(name)
        return True


def patch_run_command(monkeypatch: pytest.MonkeyPatch) -> RunRecorder:
    recorder = RunRecorder()
    monkeypatch.setattr(pipeline_module, "run_named_command", recorder)
    return recorder


def patch_rewrite(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    """Skip the real rewrite model call; `run_rewrite` returns `output`."""
    monkeypatch.setattr(
        pipeline_module, "run_rewrite", lambda rule, config, text: output
    )
