"""Adversarial fail-open attack on `handle_hook_request` (AC-4, AC-13, AC-14).

Every case here must still answer with exit_code 0 and an allow-shaped
stdout, per the "fail-open everywhere" invariant: daemon-internal faults
never block a session.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

import pytest

from vaudeville.rules import DecideRule
from vaudeville.server.harness import get_adapter as _get_adapter
from vaudeville.server.hook import handle_hook_request
from vaudeville.server.hook import pipeline as pipeline_module
from vaudeville.server.agents import DecideResult
from vaudeville.server.user_config import UserConfig

from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import decide_fn as _decide_fn
from _hook_helpers import isolate_rule_layers  # noqa: F401
from _hook_helpers import make_request as _request
from _hook_helpers import write_rule as _write_rule

_ALLOW = {"stdout": "{}", "exit_code": 0}


def _matching_rule(tmp_path: Path) -> None:
    _write_rule(
        tmp_path,
        "gate",
        """
type: decide
name: gate
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


class TestRuleFileHazards:
    def test_directory_named_yaml_is_skipped_and_pipeline_still_allows(
        self, tmp_path: Path
    ) -> None:
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "trap.yaml").mkdir()

        result = handle_hook_request(_request(tmp_path), config=_CONFIG)

        assert result == _ALLOW

    def test_non_utf8_rule_file_is_skipped_and_pipeline_still_allows(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "bad.yaml").write_bytes(b"name: \xff\xfe garbage bytes\n")
        caplog.set_level(logging.WARNING)

        result = handle_hook_request(_request(tmp_path), config=_CONFIG)

        assert result == _ALLOW

    def test_yaml_alias_expansion_is_skipped_not_a_mapping_and_allows(
        self, tmp_path: Path
    ) -> None:
        # Bounded alias-expansion bomb: a top-level sequence (not a mapping),
        # so `load_rule_file` raises ValueError and the loader skips it.
        # Kept small (5 levels x10) so the test itself stays fast.
        lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
        for i in range(1, 5):
            lines.append(f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}]")
        lines.append("- *a4")
        body = "\n".join(lines)
        rules_dir = tmp_path / ".vaudeville" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "bomb.yaml").write_text(body)

        result = handle_hook_request(_request(tmp_path), config=_CONFIG)

        assert result == _ALLOW


class TestCwdHazards:
    def test_cwd_is_a_nonexistent_directory_allows(self, tmp_path: Path) -> None:
        request = _request(tmp_path / "does-not-exist")

        result = handle_hook_request(request, config=_CONFIG)

        assert result == _ALLOW

    def test_cwd_is_a_file_not_a_directory_allows(self, tmp_path: Path) -> None:
        file_path = tmp_path / "im-a-file"
        file_path.write_text("not a directory")

        result = handle_hook_request(_request(file_path), config=_CONFIG)

        assert result == _ALLOW

    def test_cwd_with_parent_traversal_allows(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        request["payload"]["cwd"] = str(tmp_path) + "/../../../../../../etc"  # type: ignore[index]
        request["cwd"] = str(tmp_path) + "/../../../../../../etc"

        result = handle_hook_request(request, config=_CONFIG)

        assert result == _ALLOW


class TestMalformedPayload:
    def test_payload_is_a_list_not_a_dict_allows(self, tmp_path: Path) -> None:
        request = {
            "op": "hook",
            "harness": "claude-code",
            "event": "PreToolUse",
            "cwd": str(tmp_path),
            "payload": ["not", "a", "dict"],
        }

        result = handle_hook_request(request, config=_CONFIG)

        assert result == _ALLOW

    def test_payload_is_none_allows(self, tmp_path: Path) -> None:
        request = {
            "op": "hook",
            "harness": "claude-code",
            "event": "PreToolUse",
            "cwd": str(tmp_path),
            "payload": None,
        }

        result = handle_hook_request(request, config=_CONFIG)

        assert result == _ALLOW

    def test_unknown_event_name_allows(self, tmp_path: Path) -> None:
        _matching_rule(tmp_path)
        request = _request(tmp_path)
        request["payload"]["hook_event_name"] = "TotallyMadeUpEvent"  # type: ignore[index]
        request["event"] = "TotallyMadeUpEvent"

        result = handle_hook_request(request, config=_CONFIG)

        assert result == _ALLOW


class TestInternalFaultsFailOpen:
    def test_decide_fn_raising_allows(self, tmp_path: Path) -> None:
        _matching_rule(tmp_path)

        def boom(rule: DecideRule, config: UserConfig, text: str) -> DecideResult:
            raise RuntimeError("decide exploded")

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=boom)

        assert result == _ALLOW

    def test_event_logger_raising_mid_decision_allows(self, tmp_path: Path) -> None:
        _matching_rule(tmp_path)
        fn, _ = _decide_fn('{"outcome": "violation"}')

        class ExplodingLogger:
            def log_event(self, event: object) -> None:
                raise RuntimeError("logger exploded")

        result = handle_hook_request(
            _request(tmp_path),
            config=_CONFIG,
            decide_fn=fn,
            event_logger=ExplodingLogger(),  # type: ignore[arg-type]
        )

        assert result == _ALLOW

    def test_adapter_render_raising_allows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _matching_rule(tmp_path)
        fn, _ = _decide_fn('{"outcome": "violation"}')

        real_get_adapter = _get_adapter

        class ExplodingAdapter:
            def normalize(self, raw: Mapping[str, object]) -> object:
                adapter = real_get_adapter("claude-code")
                assert adapter is not None
                return adapter.normalize(raw)

            def render(self, outcome: object) -> dict[str, object]:
                raise RuntimeError("render exploded")

        monkeypatch.setattr(
            pipeline_module, "get_adapter", lambda name: ExplodingAdapter()
        )

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result == _ALLOW

    def test_run_named_command_raising_allows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_rule(
            tmp_path,
            "run-gate",
            """
type: decide
name: run-gate
event: PreToolUse
matcher: Write
model: fake:model
prompt: Classify.
outcomes: [violation, clean]
"on":
  violation: {action: run, command: notify}
tier: block
""",
        )
        fn, _ = _decide_fn('{"outcome": "violation"}')

        def boom(*args: object, **kwargs: object) -> bool:
            raise OSError("Popen exploded")

        monkeypatch.setattr(pipeline_module, "run_named_command", boom)

        result = handle_hook_request(_request(tmp_path), config=_CONFIG, decide_fn=fn)

        assert result == _ALLOW

    def test_unknown_harness_allows(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        request["harness"] = "not-a-real-harness"

        result = handle_hook_request(request, config=_CONFIG)

        assert result == _ALLOW

    def test_missing_harness_key_allows(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        del request["harness"]

        result = handle_hook_request(request, config=_CONFIG)

        assert result == _ALLOW
