"""AC-6 tier ceiling matrix: one row per rollout tier x action combination."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vaudeville.server.hook import handle_hook_request

from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import decide_fn as _decide_fn
from _hook_helpers import make_request as _request
from _hook_helpers import patch_rewrite, patch_run_command
from _hook_helpers import write_rule as _write_rule
from vaudeville.server.agents import DecideResult


def _decide_rule(name: str, action_yaml: str, tier: str) -> str:
    return f"""
type: decide
name: {name}
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action_yaml}
tier: {tier}
"""


class TestTierCeiling:
    def test_disabled_skips_model_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "disabled-rule",
            _decide_rule("disabled-rule", "block", "disabled"),
        )
        fn, recorder = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert recorder.call_count == 0
        assert result == {"stdout": "{}", "exit_code": 0}

    def test_shadow_logs_without_action(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vaudeville.server.event_log import EventLogger
        from vaudeville.server.log_config import LogConfig

        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path, "shadow-rule", _decide_rule("shadow-rule", "block", "shadow")
        )
        fn, recorder = _decide_fn('{"outcome": "violation"}')
        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            result = handle_hook_request(
                _request(tmp_path), config=_CONFIG, decide_fn=fn, event_logger=logger
            )
        finally:
            logger.close()

        assert recorder.call_count == 1
        assert result == {"stdout": "{}", "exit_code": 0}

        import json
        import time

        time.sleep(0.05)
        lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
        record = json.loads(lines[-1])
        assert record["action"] == "block"
        assert record["downgrade"] == "tier:shadow"

    def test_log_tier_does_not_start_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "log-rule",
            _decide_rule("log-rule", "{action: run, command: notify}", "log"),
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')
        run_recorder = patch_run_command(monkeypatch)

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert run_recorder.calls == []
        assert result == {"stdout": "{}", "exit_code": 0}

    def test_warn_caps_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "warn-block", _decide_rule("warn-block", "block", "warn"))
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert "systemMessage" in str(result["stdout"])
        assert "permissionDecision" not in str(result["stdout"])

    def test_warn_caps_ask(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "warn-ask", _decide_rule("warn-ask", "ask", "warn"))
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert "systemMessage" in str(result["stdout"])
        assert "permissionDecision" not in str(result["stdout"])

    def test_warn_caps_rewrite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "warn-rewrite",
            _decide_rule(
                "warn-rewrite", "{action: rewrite, rule: rewrite-target}", "warn"
            ),
        )
        _write_rule(
            tmp_path,
            "rewrite-target",
            """
type: rewrite
name: rewrite-target
event: PreToolUse
matcher: Write
model: fake:model
prompt: Rewrite.
target: [tool_input.content]
tier: block
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')
        patch_rewrite(monkeypatch, "safe command")

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert "systemMessage" in str(result["stdout"])
        assert "updatedInput" not in str(result["stdout"])

    def test_warn_caps_feedback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path, "warn-feedback", _decide_rule("warn-feedback", "feedback", "warn")
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert "systemMessage" in str(result["stdout"])

    def test_warn_caps_escalate_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "warn-escalate",
            _decide_rule(
                "warn-escalate", "{action: escalate, rule: escalate-target}", "warn"
            ),
        )
        _write_rule(
            tmp_path,
            "escalate-target",
            """
type: decide
name: escalate-target
event: PreToolUse
matcher: Read
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
\"on\":
  violation: block
tier: block
""",
        )
        calls: dict[str, int] = {}

        def counting_decide_fn(rule: object, config: object, text: str) -> DecideResult:
            del config, text
            name = getattr(rule, "name", "")
            calls[name] = calls.get(name, 0) + 1
            return DecideResult(outcome="violation")

        result = handle_hook_request(
            _request(tmp_path), config=_CONFIG, decide_fn=counting_decide_fn
        )

        assert calls.get("escalate-target") == 1
        assert result["exit_code"] == 0
        payload = dict(json.loads(str(result["stdout"])))
        assert payload["systemMessage"] == "violation"
        assert "permissionDecision" not in str(result["stdout"])

    def test_warn_keeps_add_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "warn-context",
            _decide_rule(
                "warn-context", '{action: add-context, text: "extra context"}', "warn"
            ),
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert "extra context" in str(result["stdout"])
        assert "additionalContext" in str(result["stdout"])

    def test_warn_keeps_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "warn-run",
            _decide_rule("warn-run", "{action: run, command: notify}", "warn"),
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')
        run_recorder = patch_run_command(monkeypatch)

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert run_recorder.calls == ["notify"]
        assert result == {"stdout": "{}", "exit_code": 0}

    def test_block_tier_denies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path, "block-rule", _decide_rule("block-rule", "block", "block")
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert "deny" in str(result["stdout"])
