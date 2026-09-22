"""Tests for the outcome-to-action map, matcher gating, and decision record fields.

AC-5, AC-17, AC-21 (decision_record field shape), AC-26.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from vaudeville.server.event_log import EventLogger
from vaudeville.server.hook import handle_hook_request
from vaudeville.server.log_config import LogConfig

from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import decide_fn as _decide_fn
from _hook_helpers import isolate_rule_layers  # noqa: F401
from _hook_helpers import make_request as _request
from _hook_helpers import patch_rewrite, patch_run_command
from _hook_helpers import write_rule as _write_rule


DECIDE_RULE_YAML = """
type: decide
name: pipeline-git-gate
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean, ticket-instead]
"on":
  violation: block
tier: block
"""


class TestOnMap:
    def test_on_map_applies_mapped_action(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "pipeline-git-gate", DECIDE_RULE_YAML)
        fn, recorder = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert recorder.call_count == 1
        assert result["exit_code"] == 0
        assert "deny" in str(result["stdout"])

    def test_unmapped_outcome_allows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tracer: outcome `ticket-instead` has no `on:` entry, so it allows."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "pipeline-git-gate", DECIDE_RULE_YAML)
        fn, recorder = _decide_fn('{"outcome": "ticket-instead"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert recorder.call_count == 1
        assert result == {"stdout": "{}", "exit_code": 0}


class TestMatcher:
    def test_matcher_no_model_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "pipeline-git-gate", DECIDE_RULE_YAML)
        fn, recorder = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(
            _request(tmp_path, tool_name="Read"), config=_CONFIG, decide_fn=fn
        )

        assert recorder.call_count == 0
        assert result == {"stdout": "{}", "exit_code": 0}


class TestDecisionRecord:
    def test_decision_record_written_with_expected_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "pipeline-git-gate", DECIDE_RULE_YAML)
        fn, _ = _decide_fn('{"outcome": "violation"}')
        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            handle_hook_request(
                _request(tmp_path), config=_CONFIG, decide_fn=fn, event_logger=logger
            )
            import time

            time.sleep(0.05)
            import json

            lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
            record = json.loads(lines[-1])
            assert record["rule"] == "pipeline-git-gate"
            assert record["outcome"] == "violation"
            assert record["action"] == "block"
            assert record["model"] == "fake:model"
            assert "confidence" in record
            assert "latency_ms" in record
        finally:
            logger.close()


REASON_RULE_YAML = """
type: decide
name: secret-gate
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
reasons:
  secret-leak: leaked a secret
  other: other issue
"on":
  violation: block
tier: block
"""


class TestReasonDescription:
    def test_reason_description_uses_bucket_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "secret-gate", REASON_RULE_YAML)
        fn, _ = _decide_fn('{"outcome": "violation", "reason": "secret-leak"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert "leaked a secret" in str(result["stdout"])
        assert "secret-leak" not in str(result["stdout"])


class TestRuntimeRewriteGuard:
    def test_rewrite_target_matcher_mismatch_against_live_event_allows(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "bash-gate",
            """
type: decide
name: bash-gate
event: PreToolUse
matcher: Bash
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: rewrite, rule: rewrite-target}
tier: block
""",
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
        caplog.set_level(logging.WARNING)

        result = handle_hook_request(
            _request(tmp_path, tool_name="Bash"), config=_CONFIG, decide_fn=fn
        )

        assert "updatedInput" not in str(result["stdout"])
        assert result == {"stdout": "{}", "exit_code": 0}
        assert any(
            "event/matcher mismatch" in r.getMessage() for r in caplog.records
        )


class TestEscalateDispatch:
    """F9: post-escalate branches dispatch against the target's own action."""

    def test_escalated_rewrite_produces_updated_input(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "outer-gate",
            """
type: decide
name: outer-gate
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: escalate, rule: escalate-target}
tier: block
""",
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
"on":
  violation: {action: rewrite, rule: rewrite-target}
tier: block
""",
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
        patch_rewrite(monkeypatch, "sanitized text")

        request = _request(
            tmp_path, tool_input={"content": "old text", "file_path": "notes.txt"}
        )
        result = handle_hook_request(request, config=_CONFIG, decide_fn=fn)

        assert "updatedInput" in str(result["stdout"])

    def test_escalated_run_reaches_target_named_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "outer-gate",
            """
type: decide
name: outer-gate
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: escalate, rule: escalate-target}
tier: block
""",
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
"on":
  violation: {action: run, command: notify-target}
tier: block
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')
        run_recorder = patch_run_command(monkeypatch)

        result = handle_hook_request(
            _request(tmp_path), config=_CONFIG, decide_fn=fn
        )

        assert result == {"stdout": "{}", "exit_code": 0}
        assert run_recorder.calls == ["notify-target"]


class TestEscalateTierCeiling:
    """F10: a disabled or warn-tier escalate target is capped before the
    caller's own tier ceiling."""

    def test_escalate_into_disabled_target_allows_without_a_decide_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "outer-gate",
            """
type: decide
name: outer-gate
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: escalate, rule: escalate-target}
tier: block
""",
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
"on":
  violation: block
tier: disabled
""",
        )
        calls: dict[str, int] = {"outer-gate": 0, "escalate-target": 0}

        def decide_fn(rule: object, config: object, text: str) -> object:
            del config, text
            from vaudeville.server.agents import DecideResult

            name = getattr(rule, "name", "")
            calls[name] = calls.get(name, 0) + 1
            return DecideResult(outcome="violation")

        result = handle_hook_request(
            _request(tmp_path), config=_CONFIG, decide_fn=decide_fn
        )

        assert calls["escalate-target"] == 0
        assert result == {"stdout": "{}", "exit_code": 0}

    def test_escalate_into_warn_tier_target_downgrades_block_to_warn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "outer-gate",
            """
type: decide
name: outer-gate
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: escalate, rule: escalate-target}
tier: block
""",
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
"on":
  violation: block
tier: warn
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert "systemMessage" in str(result["stdout"])
        assert "permissionDecision" not in str(result["stdout"])
