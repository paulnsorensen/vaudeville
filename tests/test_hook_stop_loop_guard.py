"""Stop loop guard: a Stop event with `stop_hook_active` never blocks again."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import decide_fn as _decide_fn
from _hook_helpers import write_rule as _write_rule

from vaudeville.server.event_log import EventLogger
from vaudeville.server.hook import handle_hook_request
from vaudeville.server.log_config import LogConfig


def _rule(event: str, tier: str = "block") -> str:
    return f"""
type: decide
name: stop-rule
event: {event}
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: block
tier: {tier}
"""


def _request(tmp_path: Path, harness: str, event: str, active: object | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "hook_event_name": event,
        "last_assistant_message": "Should I commit?",
        "cwd": str(tmp_path),
    }
    if active is not None:
        payload["stop_hook_active"] = active
    return {
        "op": "hook",
        "harness": harness,
        "event": event,
        "cwd": str(tmp_path),
        "payload": payload,
    }


def _run(
    tmp_path: Path, request: dict[str, object], monkeypatch: pytest.MonkeyPatch, event: str
) -> tuple[dict[str, object], dict[str, object]]:
    monkeypatch.setenv("FAKE_KEY", "x")
    _write_rule(tmp_path, "stop-rule", _rule(event))
    fn, _ = _decide_fn('{"outcome": "violation"}')
    logs_dir = tmp_path / "logs"
    logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
    try:
        result = handle_hook_request(request, config=_CONFIG, decide_fn=fn, event_logger=logger)
    finally:
        logger.close()
    time.sleep(0.05)
    lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
    return result, json.loads(lines[-1])


def _blocks(result: dict[str, object]) -> bool:
    stdout = str(result["stdout"])
    return bool(stdout) and json.loads(stdout).get("decision") == "block"


@pytest.fixture(
    params=[(h, e) for h in ("claude-code", "codex") for e in ("Stop", "SubagentStop")],
    ids=lambda p: f"{p[0]}-{p[1]}",
)
def target(request: pytest.FixtureRequest) -> tuple[str, str]:
    harness, event = request.param
    return harness, event


class TestStopLoopGuard:
    @pytest.mark.parametrize("active", [None, False])
    def test_blocks_when_flag_missing_or_false(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        target: tuple[str, str],
        active: bool | None,
    ) -> None:
        harness, event = target
        result, record = _run(
            tmp_path, _request(tmp_path, harness, event, active), monkeypatch, event
        )

        assert _blocks(result)
        assert record["action"] == "block"
        assert not record.get("downgrade")

    def test_caps_block_when_flag_true_and_still_logs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: tuple[str, str]
    ) -> None:
        harness, event = target
        result, record = _run(
            tmp_path, _request(tmp_path, harness, event, True), monkeypatch, event
        )

        assert not _blocks(result)
        assert result["exit_code"] == 0
        assert record["rule"] == "stop-rule"
        assert record["verdict"] == "violation"
        assert record["action"] == "warn"
        assert record["downgrade"] == "stop-hook-active"

    def test_non_boolean_flag_does_not_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: tuple[str, str]
    ) -> None:
        harness, event = target
        result, _ = _run(tmp_path, _request(tmp_path, harness, event, "true"), monkeypatch, event)

        assert _blocks(result)


def test_non_stop_event_ignores_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path, "claude-code", "PreToolUse", True)
    payload = request["payload"]
    assert isinstance(payload, dict)
    payload.update({"tool_name": "Write", "tool_input": {"content": "x"}})

    result, record = _run(tmp_path, request, monkeypatch, "PreToolUse")

    assert record["action"] == "block"
    assert "deny" in str(result["stdout"])
