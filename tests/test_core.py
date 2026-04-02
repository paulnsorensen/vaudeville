"""Tests for vaudeville.core — protocol, client, rules."""

from __future__ import annotations

import json
import os
import tempfile

from vaudeville.core.client import VaudevilleClient
from vaudeville.core.protocol import ClassifyRequest, parse_verdict
from vaudeville.core.rules import load_rules


# --- parse_verdict ---


class TestParseVerdict:
    def test_clean_verdict(self) -> None:
        result = parse_verdict("VERDICT: clean\nREASON: no issues found")
        assert result.verdict == "clean"
        assert result.reason == "no issues found"

    def test_violation_verdict(self) -> None:
        result = parse_verdict(
            "VERDICT: violation\nREASON: premature completion detected"
        )
        assert result.verdict == "violation"
        assert result.reason == "premature completion detected"

    def test_case_insensitive(self) -> None:
        result = parse_verdict("verdict: VIOLATION\nreason: test")
        assert result.verdict == "violation"

    def test_fallback_keyword_scan(self) -> None:
        result = parse_verdict("This response contains a violation of the rules.")
        assert result.verdict == "violation"

    def test_fallback_clean_on_no_keyword(self) -> None:
        result = parse_verdict("Everything looks good here.")
        assert result.verdict == "clean"

    def test_malformed_defaults_to_clean(self) -> None:
        result = parse_verdict("")
        assert result.verdict == "clean"

    def test_reason_trimmed(self) -> None:
        result = parse_verdict("VERDICT: clean\nREASON:   leading spaces  ")
        assert result.reason == "leading spaces"

    def test_mixed_case_verdict_value(self) -> None:
        result = parse_verdict("VERDICT: Violation\nREASON: test")
        assert result.verdict == "violation"


# --- ClassifyRequest ---


class TestClassifyRequest:
    def test_to_json_dict(self) -> None:
        req = ClassifyRequest(rule="test-rule", input={"text": "hello"})
        d = req.to_json_dict()
        assert d["rule"] == "test-rule"
        assert d["input"] == {"text": "hello"}

    def test_json_serializable(self) -> None:
        req = ClassifyRequest(rule="r", input={"text": "t", "context": {}})
        json.dumps(req.to_json_dict())  # must not raise


# --- load_rules ---


class TestLoadRules:
    def test_loads_all_rules(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        assert "violation-detector" in rules
        assert "dismissal-detector" in rules
        assert "deferral-detector" in rules

    def test_rule_fields(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        rule = rules["violation-detector"]
        assert rule.name == "violation-detector"
        assert rule.event == "Stop"
        assert "{text}" in rule.prompt
        assert rule.action == "block"

    def test_format_prompt_interpolates_text(self, rules_dir: str) -> None:
        rules = load_rules(rules_dir)
        rule = rules["violation-detector"]
        formatted = rule.format_prompt("test response text")
        assert "test response text" in formatted
        assert "{text}" not in formatted

    def test_empty_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            assert load_rules(d) == {}

    def test_nonexistent_dir_returns_empty(self) -> None:
        assert load_rules("/nonexistent/path/xyz") == {}


# --- Fail-open path ---


class TestFailOpen:
    def test_client_returns_none_for_missing_socket(self) -> None:
        client = VaudevilleClient()
        client._socket_path = "/tmp/nonexistent-session-id-xyz.sock"
        result = client.classify("violation-detector", {"text": "test"})
        assert result is None

    def test_client_fast_path_missing_socket(self) -> None:
        """Socket-exists guard returns None in <100ms, not the 1s connect timeout."""
        import time

        client = VaudevilleClient()
        client._socket_path = "/tmp/nonexistent-fast-path-test.sock"
        start = time.monotonic()
        result = client.classify("violation-detector", {"text": "test"})
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 0.1, f"Expected <100ms, got {elapsed:.3f}s (socket timeout?)"

    def test_client_fast_path_with_real_socket_file(self) -> None:
        """When socket file exists but nothing listens, client gets ConnectionRefused."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sock", dir="/tmp", delete=False) as f:
            fake_socket = f.name
        try:
            client = VaudevilleClient()
            client._socket_path = fake_socket
            result = client.classify("test-rule", {"text": "test"})
            # File exists but not a real socket — should fail with connection error
            assert result is None
        finally:
            os.unlink(fake_socket)

    def test_runner_allows_when_daemon_unavailable(self) -> None:
        """runner.main() exits 0 when client returns None for all rules."""
        import importlib
        import io
        import sys
        from unittest.mock import MagicMock, patch

        hook_input = json.dumps(
            {
                "session_id": "nonexistent-session-xyz",
                "last_assistant_message": "A" * 200,
            }
        )

        mock_client = MagicMock()
        mock_client.classify.return_value = None

        hooks_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"
        )

        with (
            patch("sys.stdin", io.StringIO(hook_input)),
            patch("sys.stdout", io.StringIO()),
            patch("sys.argv", ["runner.py", "violation-detector"]),
            patch("vaudeville.core.client.VaudevilleClient", return_value=mock_client),
        ):
            if hooks_dir not in sys.path:
                sys.path.insert(0, hooks_dir)
            try:
                runner = importlib.import_module("runner")
                importlib.reload(runner)
                runner.main()
            except SystemExit as e:
                assert e.code == 0
            finally:
                if hooks_dir in sys.path:
                    sys.path.remove(hooks_dir)
