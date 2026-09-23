"""Tests for the outcome-to-action map, matcher gating, and decision record fields.

AC-5, AC-17, AC-21 (decision_record field shape), AC-26.
"""

from __future__ import annotations

import functools
import json
import logging
import time
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vaudeville.core.truncation import CHARS_PER_TOKEN, MAX_INPUT_TOKENS
from vaudeville.rules import DecideRule
from vaudeville.server.agents import DecideResult, ModelResolution, decide
from vaudeville.server.agents.delimit import HOOK_DATA_END, HOOK_DATA_START
from vaudeville.server.event_log import EventLogger
from vaudeville.server.hook import handle_hook_request
from vaudeville.server.hook import pipeline as pipeline_module
from vaudeville.server.log_config import LogConfig
from vaudeville.server.user_config import UserConfig

from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import decide_fn as _decide_fn
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
        assert result == {"stdout": "", "exit_code": 0}


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
        assert result == {"stdout": "", "exit_code": 0}


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


class TestRequestDeadline:
    def test_slow_decide_fn_times_out_and_allows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F25: a decide_fn exceeding the request deadline fails open and
        logs a decide-timeout downgrade; wall time stays under the deadline."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "pipeline-git-gate", DECIDE_RULE_YAML)

        def slow_decide_fn(rule: object, config: object, text: str) -> DecideResult:
            time.sleep(0.5)
            return DecideResult(outcome="violation")

        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            wall_start = time.monotonic()
            result = handle_hook_request(
                _request(tmp_path),
                config=_CONFIG,
                decide_fn=slow_decide_fn,
                event_logger=logger,
                deadline_seconds=0.1,
            )
            elapsed = time.monotonic() - wall_start

            assert result == {"stdout": "", "exit_code": 0}
            assert elapsed < 0.6

            time.sleep(0.05)
            lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
            records = [json.loads(line) for line in lines]
            assert any(r.get("downgrade") == "decide-timeout" for r in records)
        finally:
            logger.close()

    def test_raising_decide_fn_logs_decide_error_and_allows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """M2: a decide_fn that raises fails open and logs `decide-error`,
        distinct from a `decide-timeout` deadline expiry."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "pipeline-git-gate", DECIDE_RULE_YAML)

        def raising_decide_fn(rule: object, config: object, text: str) -> DecideResult:
            raise RuntimeError("boom")

        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            result = handle_hook_request(
                _request(tmp_path),
                config=_CONFIG,
                decide_fn=raising_decide_fn,
                event_logger=logger,
            )

            assert result == {"stdout": "", "exit_code": 0}

            time.sleep(0.05)
            lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
            records = [json.loads(line) for line in lines]
            assert any(r.get("downgrade") == "decide-error" for r in records)
        finally:
            logger.close()

    def test_escalate_deadline_never_exceeds_remaining_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F25: the escalate deadline used for each decide_fn call is capped
        by the remaining request budget, recorded via a wrapping recorder."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "pipeline-git-gate", DECIDE_RULE_YAML)
        fn, _ = _decide_fn('{"outcome": "violation"}')

        deadlines: list[float] = []
        from vaudeville.server.effects.escalate import (
            escalate_result as real_escalate_result,
        )

        def recording_escalate_result(
            run: object, *, deadline: float, rule_name: str | None = None
        ) -> object:
            deadlines.append(deadline)
            return real_escalate_result(run, deadline=deadline, rule_name=rule_name)  # type: ignore[arg-type]

        monkeypatch.setattr(
            pipeline_module, "escalate_result", recording_escalate_result
        )

        handle_hook_request(
            _request(tmp_path), config=_CONFIG, decide_fn=fn, deadline_seconds=1.5
        )

        assert deadlines
        assert all(d <= 1.5 for d in deadlines)

    def test_slow_run_rewrite_times_out_and_allows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """H3: `run_rewrite` exceeding the request deadline fails open and
        logs a rewrite-timeout downgrade; wall time stays under the deadline."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "pipeline-rewrite-gate",
            """
type: decide
name: pipeline-rewrite-gate
event: PreToolUse
matcher: Write
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
prompt: Rewrite.
target: [tool_input.content]
tier: block
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        monkeypatch.setattr(
            pipeline_module,
            "resolve_model",
            lambda rule, config: ModelResolution(model="fake:model"),
        )

        def slow_run_rewrite(rule: object, model: object, text: str) -> str:
            time.sleep(0.5)
            return "rewritten"

        monkeypatch.setattr(pipeline_module, "run_rewrite", slow_run_rewrite)

        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            wall_start = time.monotonic()
            result = handle_hook_request(
                _request(tmp_path, tool_input={"content": "old", "file_path": "a.txt"}),
                config=_CONFIG,
                decide_fn=fn,
                event_logger=logger,
                deadline_seconds=0.1,
            )
            elapsed = time.monotonic() - wall_start

            assert result == {"stdout": "", "exit_code": 0}
            assert elapsed < 0.6

            time.sleep(0.05)
            lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
            records = [json.loads(line) for line in lines]
            assert any(r.get("downgrade") == "rewrite-timeout" for r in records)
        finally:
            logger.close()


STOP_ASK_RULE_YAML = """
type: decide
name: pipeline-stop-ask
event: Stop
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: ask
tier: block
"""


class TestRenderDowngradeTelemetry:
    def test_ask_on_stop_downgrade_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F24: an `ask` on Stop degrades in the adapter; the downgrade lands
        in events.jsonl naming `from`, `to`, and `event`."""
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "pipeline-stop-ask", STOP_ASK_RULE_YAML)
        fn, _ = _decide_fn('{"outcome": "violation"}')
        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        request = {
            "op": "hook",
            "harness": "claude-code",
            "event": "Stop",
            "cwd": str(tmp_path),
            "payload": {
                "hook_event_name": "Stop",
                "last_assistant_message": "some text",
                "cwd": str(tmp_path),
            },
        }
        try:
            handle_hook_request(
                request, config=_CONFIG, decide_fn=fn, event_logger=logger
            )
            time.sleep(0.05)

            lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
            records = [json.loads(line) for line in lines]
            assert len(records) == 1
            downgrade_records = [r for r in records if r.get("downgrade")]
            assert len(downgrade_records) == 1
            record = downgrade_records[0]
            assert record["rule"] == "pipeline-stop-ask"
            assert record["action"] == "ask"
            assert "ask" in record["downgrade"]
            assert "warn" in record["downgrade"]
            assert "Stop" in record["downgrade"]
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
        assert result == {"stdout": "", "exit_code": 0}
        assert any("event/matcher mismatch" in r.getMessage() for r in caplog.records)

    def test_escalate_target_matcher_mismatch_against_live_event_allows(
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
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
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

        caplog.set_level(logging.WARNING)

        result = handle_hook_request(
            _request(tmp_path, tool_name="Bash"),
            config=_CONFIG,
            decide_fn=counting_decide_fn,
        )

        assert calls.get("escalate-target", 0) == 0
        assert result == {"stdout": "", "exit_code": 0}
        assert any("event/matcher mismatch" in r.getMessage() for r in caplog.records)


REWRITE_GATE_YAML = """
type: decide
name: pipeline-rewrite-gate
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: rewrite, rule: rewrite-target}
tier: block
"""

REWRITE_TARGET_YAML = """
type: rewrite
name: rewrite-target
event: PreToolUse
matcher: Write
model: fake:model
prompt: Rewrite.
target: [tool_input.content]
tier: block
"""


class TestRewriteLogRedaction:
    """F21: rewrite logs keep raw tool-input values out of the INFO log."""

    def _run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "pipeline-rewrite-gate", REWRITE_GATE_YAML)
        _write_rule(tmp_path, "rewrite-target", REWRITE_TARGET_YAML)
        fn, _ = _decide_fn('{"outcome": "violation"}')
        patch_rewrite(monkeypatch, "REWRITTEN_SECRET_VALUE")
        handle_hook_request(
            _request(
                tmp_path,
                tool_input={"content": "API_KEY=hunter2secret", "file_path": ".env"},
            ),
            config=_CONFIG,
            decide_fn=fn,
        )

    def test_info_log_has_lengths_and_hash_but_no_raw_values(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("VAUDEVILLE_DEBUG", raising=False)
        caplog.set_level(logging.DEBUG, logger=pipeline_module.__name__)

        self._run(tmp_path, monkeypatch)

        assert "hunter2secret" not in caplog.text
        assert "REWRITTEN_SECRET_VALUE" not in caplog.text
        summary = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.INFO and "tool_input.content" in r.getMessage()
        ]
        assert len(summary) == 1
        assert "before_len=21" in summary[0]
        assert "after_len=22" in summary[0]
        assert "sha256=" in summary[0]

    def test_debug_env_logs_full_values(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("VAUDEVILLE_DEBUG", "1")
        caplog.set_level(logging.DEBUG, logger=pipeline_module.__name__)

        self._run(tmp_path, monkeypatch)

        assert "hunter2secret" in caplog.text
        assert "REWRITTEN_SECRET_VALUE" in caplog.text

    def test_fingerprint_marks_a_missing_value_and_hashes_structured_values(
        self,
    ) -> None:
        assert pipeline_module._fingerprint(None) == (0, "-")
        length, digest = pipeline_module._fingerprint({"k": "v"})
        assert length == len('{"k": "v"}')
        assert len(digest) == 12


class TestRewritePerTarget:
    """F16: each rewrite target is rewritten from its own current value."""

    def _write_rules(self, tmp_path: Path, targets: str) -> None:
        _write_rule(tmp_path, "pipeline-rewrite-gate", REWRITE_GATE_YAML)
        _write_rule(
            tmp_path,
            "rewrite-target",
            REWRITE_TARGET_YAML.replace("[tool_input.content]", targets),
        )

    def test_two_targets_with_different_values_each_get_their_own_rewrite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        self._write_rules(tmp_path, "[tool_input.content, tool_input.description]")
        fn, _ = _decide_fn('{"outcome": "violation"}')
        monkeypatch.setattr(
            pipeline_module,
            "resolve_model",
            lambda rule, config: ModelResolution(model="fake:model"),
        )
        seen: list[str] = []

        def upper(rule: object, model: object, text: str) -> str:
            seen.append(text)
            return text.upper()

        monkeypatch.setattr(pipeline_module, "run_rewrite", upper)

        result = handle_hook_request(
            _request(
                tmp_path,
                tool_input={"content": "alpha", "description": "beta"},
            ),
            config=_CONFIG,
            decide_fn=fn,
        )

        payload = json.loads(str(result["stdout"]))
        updated = payload["hookSpecificOutput"]["updatedInput"]
        assert updated == {"content": "ALPHA", "description": "BETA"}
        assert sorted(seen) == ["alpha", "beta"]

    def test_no_string_value_at_any_target_allows_without_a_model_call(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        self._write_rules(tmp_path, "[tool_input.content]")
        fn, _ = _decide_fn('{"outcome": "violation"}')
        monkeypatch.setattr(
            pipeline_module,
            "resolve_model",
            lambda rule, config: ModelResolution(model="fake:model"),
        )
        calls: list[str] = []

        def recording_rewrite(rule: object, model: object, text: str) -> str:
            calls.append(text)
            return "x"

        monkeypatch.setattr(pipeline_module, "run_rewrite", recording_rewrite)
        caplog.set_level(logging.WARNING)

        # The Bash-style input has decidable text but no `content` value.
        result = handle_hook_request(
            _request(tmp_path, tool_input={"command": "echo hi"}),
            config=_CONFIG,
            decide_fn=fn,
        )

        assert result == {"stdout": "", "exit_code": 0}
        assert calls == []
        assert any("no string value" in r.getMessage() for r in caplog.records)

    def test_discarded_rewrite_output_allows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        self._write_rules(tmp_path, "[tool_input.content, tool_input.description]")
        fn, _ = _decide_fn('{"outcome": "violation"}')
        monkeypatch.setattr(
            pipeline_module,
            "resolve_model",
            lambda rule, config: ModelResolution(model="fake:model"),
        )
        monkeypatch.setattr(
            pipeline_module,
            "run_rewrite",
            lambda rule, model, text: None if text == "beta" else text.upper(),
        )

        result = handle_hook_request(
            _request(tmp_path, tool_input={"content": "alpha", "description": "beta"}),
            config=_CONFIG,
            decide_fn=fn,
        )

        assert result == {"stdout": "", "exit_code": 0}


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
matcher: Write
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
        """`escalate-target`'s matcher also matches the live event, so the
        main loop evaluates it a second time as an ordinary rule, running
        its `run` command a second time (by design after M4).
        """
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
matcher: Write
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

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result == {"stdout": "", "exit_code": 0}
        assert run_recorder.calls == ["notify-target", "notify-target"]


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

        def decide_fn(rule: DecideRule, config: UserConfig, text: str) -> DecideResult:
            del config, text
            calls[rule.name] = calls.get(rule.name, 0) + 1
            return DecideResult(outcome="violation")

        result = handle_hook_request(
            _request(tmp_path), config=_CONFIG, decide_fn=decide_fn
        )

        assert calls["escalate-target"] == 0
        assert result == {"stdout": "", "exit_code": 0}

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
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: block
tier: warn
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')
        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            result = handle_hook_request(
                _request(tmp_path), config=_CONFIG, decide_fn=fn, event_logger=logger
            )
        finally:
            logger.close()

        assert "systemMessage" in str(result["stdout"])
        assert "permissionDecision" not in str(result["stdout"])

        # F2: the escalate target's own tier-ceiling reason reaches the
        # decision record's `downgrade` field.
        time.sleep(0.05)
        lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
        records = [json.loads(line) for line in lines]
        rows = [r for r in records if r["rule"] == "outer-gate"]
        assert len(rows) == 1
        assert rows[0]["action"] == "warn"
        assert rows[0]["downgrade"]
        assert "tier:warn" in rows[0]["downgrade"]

    def test_nested_escalate_allows_and_logs_one_hop_downgrade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F17: an escalate target that maps to `escalate` resolves to allow
        and still writes the outer rule's decision record (AC-21)."""
        monkeypatch.setenv("FAKE_KEY", "x")
        for name, on_violation, matcher in (
            ("outer-gate", "{action: escalate, rule: escalate-target}", "Write"),
            ("escalate-target", "{action: escalate, rule: third-gate}", "Write"),
            ("third-gate", "block", "Read"),
        ):
            _write_rule(
                tmp_path,
                name,
                f"""
type: decide
name: {name}
event: PreToolUse
matcher: {matcher}
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {on_violation}
tier: block
""",
            )
        fn, _ = _decide_fn('{"outcome": "violation"}')
        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            result = handle_hook_request(
                _request(tmp_path), config=_CONFIG, decide_fn=fn, event_logger=logger
            )
        finally:
            logger.close()

        assert result == {"stdout": "", "exit_code": 0}
        time.sleep(0.05)
        lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
        rows = [json.loads(line) for line in lines]
        outer = [r for r in rows if r["rule"] == "outer-gate"]
        assert len(outer) == 1
        assert outer[0]["action"] == "allow"
        assert outer[0]["downgrade"] == "escalate:one-hop"


STOP_RULE_YAML = """
type: decide
name: stop-gate
event: Stop
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: block
tier: block
"""


class TestTruncation:
    def test_oversized_stop_text_is_truncated_and_logged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "stop-gate", STOP_RULE_YAML)
        max_chars = MAX_INPUT_TOKENS * CHARS_PER_TOKEN
        oversized = "x" * (max_chars * 3)
        prompts: list[str] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            del info
            for message in messages:
                for part in message.parts:
                    text = getattr(part, "content", None)
                    if isinstance(text, str):
                        prompts.append(text)
            return ModelResponse(parts=[TextPart('{"outcome": "violation"}')])

        fn = functools.partial(decide, model_override=FunctionModel(respond))
        request = {
            "op": "hook",
            "harness": "claude-code",
            "event": "Stop",
            "cwd": str(tmp_path),
            "payload": {
                "hook_event_name": "Stop",
                "last_assistant_message": oversized,
                "cwd": str(tmp_path),
            },
        }
        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            handle_hook_request(
                request, config=_CONFIG, decide_fn=fn, event_logger=logger
            )
            time.sleep(0.05)
            lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
            record = json.loads(lines[-1])
        finally:
            logger.close()

        sent = "\n".join(prompts)
        start = sent.index(HOOK_DATA_START) + len(HOOK_DATA_START)
        end = sent.index(HOOK_DATA_END)
        body = sent[start:end].strip("\n")
        assert len(body) == max_chars
        assert record["prompt_chars"] == max_chars


class TestEmptyTextSkip:
    def test_empty_text_skips_decide_and_allows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "read-gate",
            """
type: decide
name: read-gate
event: PreToolUse
matcher: Read
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: block
tier: block
""",
        )
        fn, recorder = _decide_fn('{"outcome": "violation"}')

        request = _request(tmp_path, tool_name="Read", tool_input={})
        result = handle_hook_request(request, config=_CONFIG, decide_fn=fn)

        assert recorder.call_count == 0
        assert result == {"stdout": "", "exit_code": 0}


class TestAllowRendering:
    """F22: unknown harness falls back to the generic allow; a known harness
    with no matching rule uses the adapter's own allow shape."""

    def test_unknown_harness_returns_generic_allow(self, tmp_path: Path) -> None:
        from vaudeville.core.protocol import GENERIC_ALLOW

        request = _request(tmp_path)
        request["harness"] = "nope"

        result = handle_hook_request(request, config=_CONFIG)

        assert result == dict(GENERIC_ALLOW)

    def test_known_harness_no_match_uses_adapter_allow(self, tmp_path: Path) -> None:
        result = handle_hook_request(_request(tmp_path), config=_CONFIG)

        assert result == {"stdout": "", "exit_code": 0}


class TestRunFailureKeepsBlock:
    """F6: a `run` of a missing binary never turns another rule's block into allow."""

    def test_block_survives_missing_run_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "pipeline-git-gate", DECIDE_RULE_YAML)
        _write_rule(
            tmp_path,
            "run-ghost",
            DECIDE_RULE_YAML.replace("pipeline-git-gate", "run-ghost").replace(
                "violation: block", "violation: {action: run, command: ghost}"
            ),
        )
        config = _CONFIG.model_copy(
            update={"commands": {"ghost": ["/nonexistent/vaudeville-ghost-bin"]}}
        )
        fn, recorder = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=config, decide_fn=fn)

        assert recorder.call_count == 2
        assert "deny" in str(result["stdout"])
