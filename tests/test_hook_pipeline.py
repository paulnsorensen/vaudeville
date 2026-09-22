"""Tests for the outcome-to-action map, matcher gating, and decision record fields.

AC-5, AC-17, AC-21 (decision_record field shape), AC-26.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vaudeville.server.event_log import EventLogger
from vaudeville.server.hook import handle_hook_request
from vaudeville.server.log_config import LogConfig

from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import decide_fn as _decide_fn
from _hook_helpers import make_request as _request
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
