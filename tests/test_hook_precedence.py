"""AC-16 precedence matrix: primary-channel ranking and context concatenation."""

from __future__ import annotations

from pathlib import Path

import pytest

from vaudeville.server.hook import handle_hook_request

from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import decide_fn as _decide_fn
from _hook_helpers import make_request as _request
from _hook_helpers import patch_rewrite
from _hook_helpers import write_rule as _write_rule


def _decide_rule(name: str, action_yaml: str) -> str:
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
tier: block
"""


class TestPrecedence:
    def test_block_over_warn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "a-warn-rule", _decide_rule("a-warn-rule", "warn"))
        _write_rule(tmp_path, "b-block-rule", _decide_rule("b-block-rule", "block"))
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert "deny" in str(result["stdout"])
        assert "systemMessage" not in str(result["stdout"])

    def test_ask_over_rewrite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "a-ask-rule", _decide_rule("a-ask-rule", "ask"))
        _write_rule(
            tmp_path,
            "b-rewrite-rule",
            _decide_rule("b-rewrite-rule", "{action: rewrite, rule: rewrite-target}"),
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
target: [command]
tier: block
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')
        patch_rewrite(monkeypatch, "safe command")

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert "permissionDecision" in str(result["stdout"])
        assert '"ask"' in str(result["stdout"])
        assert "updatedInput" not in str(result["stdout"])

    def test_dropped_rule_logged_with_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vaudeville.server.event_log import EventLogger
        from vaudeville.server.log_config import LogConfig

        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "a-warn-rule", _decide_rule("a-warn-rule", "warn"))
        _write_rule(tmp_path, "b-block-rule", _decide_rule("b-block-rule", "block"))
        fn, _ = _decide_fn('{"outcome": "violation"}')
        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            handle_hook_request(
                _request(tmp_path), config=_CONFIG, decide_fn=fn, event_logger=logger
            )
        finally:
            logger.close()

        import json
        import time

        time.sleep(0.05)
        lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
        records = [json.loads(line) for line in lines]
        dropped = [
            r
            for r in records
            if r["rule"] == "a-warn-rule"
            and r["downgrade"]
            and "precedence" in r["downgrade"]
        ]
        assert len(dropped) == 1
        assert "b-block-rule" in dropped[0]["downgrade"]

    def test_one_row_per_rule_dropped_row_carries_kind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F24: a losing warn rule gets one `kind: dropped` row, excluded from
        violations; the winning block rule gets exactly one row."""
        from vaudeville.server.event_log import EventLogger
        from vaudeville.server.log_config import LogConfig

        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(tmp_path, "a-warn-rule", _decide_rule("a-warn-rule", "warn"))
        _write_rule(tmp_path, "b-block-rule", _decide_rule("b-block-rule", "block"))
        fn, _ = _decide_fn('{"outcome": "violation"}')
        logs_dir = tmp_path / "logs"
        logger = EventLogger(config=LogConfig(), logs_dir=str(logs_dir))
        try:
            handle_hook_request(
                _request(tmp_path), config=_CONFIG, decide_fn=fn, event_logger=logger
            )
        finally:
            logger.close()

        import json
        import time

        time.sleep(0.05)
        lines = (logs_dir / "events.jsonl").read_text().strip().splitlines()
        records = [json.loads(line) for line in lines]
        assert len(records) == 2

        block_rows = [r for r in records if r["rule"] == "b-block-rule"]
        assert len(block_rows) == 1
        assert block_rows[0]["action"] == "block"
        assert block_rows[0].get("kind") is None

        dropped_rows = [r for r in records if r["rule"] == "a-warn-rule"]
        assert len(dropped_rows) == 1
        assert dropped_rows[0]["kind"] == "dropped"

        violations_path = logs_dir / "violations.jsonl"
        violation_lines = violations_path.read_text().strip().splitlines()
        violation_records = [json.loads(line) for line in violation_lines]
        assert len(violation_records) == 1
        assert violation_records[0]["rule"] == "b-block-rule"

    def test_context_accumulates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "a-context-rule",
            _decide_rule(
                "a-context-rule", '{action: add-context, text: "context one"}'
            ),
        )
        _write_rule(
            tmp_path,
            "b-context-rule",
            _decide_rule(
                "b-context-rule", '{action: add-context, text: "context two"}'
            ),
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result["exit_code"] == 0
        assert "context one" in str(result["stdout"])
        assert "context two" in str(result["stdout"])
