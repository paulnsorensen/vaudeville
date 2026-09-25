"""Adversarial attack on tier ceiling + precedence (AC-6, AC-16)."""

from __future__ import annotations

from pathlib import Path

import pytest

from vaudeville.server.agents import DecideResult
from vaudeville.server.hook import handle_hook_request

from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import decide_fn as _decide_fn
from _hook_helpers import make_request as _request
from _hook_helpers import patch_run_command
from _hook_helpers import write_rule as _write_rule


def _decide_rule(name: str, action_yaml: str, tier: str = "block") -> str:
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


class TestEscalateAtTierCeiling:
    def test_warn_tier_rule_escalating_to_a_block_target_caps_at_warn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-6: the tier ceiling of the *escalating* rule caps the escalated
        result too -- a warn-tier escalate must never surface as a block.

        `inner-block`'s matcher also matches the live event, so the main
        loop evaluates it a second time as an ordinary block-tier rule (by
        design after M4); that direct row wins the render's primary channel
        on its own merits. The cap is proven on `outer-warn`'s own decision
        record in events.jsonl instead of on the overall render output.
        """
        from vaudeville.server.event_log import EventLogger
        from vaudeville.server.log_config import LogConfig

        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "outer-warn",
            _decide_rule(
                "outer-warn", "{action: escalate, rule: inner-block}", tier="warn"
            ),
        )
        _write_rule(
            tmp_path,
            "inner-block",
            """
type: decide
name: inner-block
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
        fn, _ = _decide_fn('{"outcome": "violation"}')
        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            result = handle_hook_request(
                _request(tmp_path), config=_CONFIG, decide_fn=fn, event_logger=logger
            )
        finally:
            logger.close()

        assert result["exit_code"] == 0

        import json
        import time

        time.sleep(0.05)
        lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
        records = [json.loads(line) for line in lines]
        outer_rows = [r for r in records if r["rule"] == "outer-warn"]
        assert len(outer_rows) == 1
        assert outer_rows[0]["action"] == "warn"


class TestEscalateChainDepth:
    def test_escalate_chain_of_depth_two_stops_after_one_hop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "outer",
            _decide_rule("outer", "{action: escalate, rule: middle}"),
        )
        _write_rule(
            tmp_path,
            "middle",
            """
type: decide
name: middle
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: escalate, rule: inner}
tier: block
""",
        )
        _write_rule(
            tmp_path,
            "inner",
            """
type: decide
name: inner
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
        calls: dict[str, int] = {}

        def counting_decide_fn(rule: object, config: object, text: str) -> DecideResult:
            del config, text
            name = getattr(rule, "name", "")
            calls[name] = calls.get(name, 0) + 1
            return DecideResult(outcome="violation")

        result = handle_hook_request(
            _request(tmp_path), config=_CONFIG, decide_fn=counting_decide_fn
        )

        assert result["exit_code"] == 0
        assert calls.get("outer", 0) == 1
        # `middle`'s matcher also matches the live event, so the main loop
        # also evaluates it as an ordinary rule; the decide result is
        # memoised per `(rule.name, event.text)`, so the escalate hop from
        # `outer` and the direct evaluation share one decide call.
        assert calls.get("middle", 0) == 1
        # The one-hop bound means "inner" -- the escalation target's own
        # escalate target -- must never run.
        assert calls.get("inner", 0) == 0


class TestDisabledVersusBlock:
    def test_disabled_rule_contributes_nothing_block_rule_still_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "off-rule",
            _decide_rule("off-rule", "block", tier="disabled"),
        )
        _write_rule(
            tmp_path,
            "on-rule",
            _decide_rule("on-rule", "block", tier="block"),
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

        assert result["exit_code"] == 0
        assert "deny" in str(result["stdout"])
        # A `disabled` rule must never even call the model.
        assert calls.get("off-rule", 0) == 0
        assert calls.get("on-rule", 0) == 1


class TestLogTierNeverRuns:
    def test_log_tier_rule_with_run_action_starts_no_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        recorder = patch_run_command(monkeypatch)
        _write_rule(
            tmp_path,
            "log-run",
            _decide_rule("log-run", "{action: run, command: notify}", tier="log"),
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert recorder.calls == []


class TestShadowTierAddContext:
    def test_add_context_under_shadow_tier_triggers_no_harness_action(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "shadow-context",
            _decide_rule(
                "shadow-context",
                '{action: add-context, text: "shadow text"}',
                tier="shadow",
            ),
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result == {"stdout": "", "exit_code": 0}
        assert "shadow text" not in str(result["stdout"])


class TestWarnTierKeepsAddContextAndRun:
    def test_warn_tier_leaves_add_context_and_run_unchanged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        recorder = patch_run_command(monkeypatch)
        _write_rule(
            tmp_path,
            "warn-context",
            _decide_rule(
                "warn-context",
                '{action: add-context, text: "warn-tier context"}',
                tier="warn",
            ),
        )
        _write_rule(
            tmp_path,
            "warn-run",
            _decide_rule("warn-run", "{action: run, command: notify}", tier="warn"),
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert "warn-tier context" in str(result["stdout"])
        assert recorder.calls == ["notify"]
