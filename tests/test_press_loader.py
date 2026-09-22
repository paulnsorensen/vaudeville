"""Adversarial attack on layered rule loading (AC-1, AC-2, AC-24)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from vaudeville.rules import DecideRule, RewriteRule, load_rules_layered
from vaudeville.server.agents import DecideResult
from vaudeville.server.hook import handle_hook_request

from _hook_helpers import CONFIG as _CONFIG
from _hook_helpers import make_request as _request
from _hook_helpers import write_rule as _write_rule


class TestLayerCollisionWithDifferentType:
    """Same rule name, different `type`, in user vs project layers.

    A project rule can never override a user (or bundled) rule of the same
    name, regardless of `type`: the user rule wins and the project rule
    is skipped with a log (AC-1 layer-trust order).
    """

    def test_user_decide_rule_wins_over_project_rewrite_rule_same_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        home = tmp_path / "home"
        (home / ".vaudeville" / "rules").mkdir(parents=True)
        (home / ".vaudeville" / "rules" / "shared.yaml").write_text(
            """
type: decide
name: shared
event: PreToolUse
matcher: Write
prompt: classify
outcomes: [violation, clean]
"on":
  violation: block
"""
        )

        project = tmp_path / "project"
        (project / ".vaudeville" / "rules").mkdir(parents=True)
        (project / ".vaudeville" / "rules" / "shared.yaml").write_text(
            """
type: rewrite
name: shared
event: PreToolUse
matcher: Write
prompt: rewrite it
target: [tool_input.content]
"""
        )

        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "empty-plugin-root"))
        caplog.set_level(logging.WARNING)

        ruleset = load_rules_layered(str(project))
        rules = ruleset.by_name()

        assert len(rules) == 1
        assert isinstance(rules["shared"], DecideRule)
        assert not isinstance(rules["shared"], RewriteRule)
        assert any(
            "shared" in r.getMessage() and "user" in r.getMessage()
            for r in caplog.records
        )

    def test_layer_collision_with_different_type_does_not_crash_the_pipeline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        home = tmp_path / "home-collision"
        (home / ".vaudeville" / "rules").mkdir(parents=True)
        (home / ".vaudeville" / "rules" / "shared.yaml").write_text(
            """
type: rewrite
name: shared
event: PreToolUse
matcher: Write
prompt: rewrite it
target: [tool_input.content]
"""
        )
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "empty-plugin-root"))
        _write_rule(
            tmp_path,
            "shared",
            """
type: decide
name: shared
event: PreToolUse
matcher: Write
model: fake:model
prompt: classify
outcomes: [violation, clean]
"on":
  violation: block
tier: block
""",
        )

        def counting_decide_fn(rule: object, config: object, text: str) -> DecideResult:
            del rule, config, text
            return DecideResult(outcome="violation")

        result = handle_hook_request(
            _request(tmp_path), config=_CONFIG, decide_fn=counting_decide_fn
        )

        # The user-layer RewriteRule wins and shadows the project decide
        # rule of the same name; since no decide rule fires, the pipeline
        # neither crashes nor blocks the request -- it falls open (allow).
        assert result == {"stdout": "{}", "exit_code": 0}


class TestOnOutcomeNotInOutcomes:
    """An `on:` key naming an outcome absent from `outcomes:` (a dead mapping)."""

    def test_on_key_outside_outcomes_loads_and_never_crashes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "phantom-outcome",
            """
type: decide
name: phantom-outcome
event: PreToolUse
matcher: Write
model: fake:model
prompt: classify
outcomes: [violation, clean]
"on":
  never-produced: block
tier: block
""",
        )

        def decide_returns_violation(
            rule: object, config: object, text: str
        ) -> DecideResult:
            del rule, config, text
            return DecideResult(outcome="violation")

        result = handle_hook_request(
            _request(tmp_path), config=_CONFIG, decide_fn=decide_returns_violation
        )

        # The rule loads without error even though its `on:` key can never
        # be produced by `outcomes:`. Since "violation" has no `on:` entry,
        # the pipeline must fall open (allow), not crash.
        assert result == {"stdout": "{}", "exit_code": 0}

    def test_on_key_outside_outcomes_is_reachable_if_a_decide_fn_defies_the_schema(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Real decide agents constrain `outcome` via a Literal(outcomes)
        pydantic-ai output type, so this path is unreachable in production.
        A test double that returns an out-of-schema outcome exercises the
        pipeline's `rule.on.get(...)` lookup directly, showing it degrades
        to allow rather than raising -- consistent behaviour either way.
        """
        monkeypatch.setenv("FAKE_KEY", "x")
        _write_rule(
            tmp_path,
            "phantom-outcome-2",
            """
type: decide
name: phantom-outcome-2
event: PreToolUse
matcher: Write
model: fake:model
prompt: classify
outcomes: [violation, clean]
"on":
  never-produced: block
tier: block
""",
        )

        def decide_returns_out_of_schema_outcome(
            rule: object, config: object, text: str
        ) -> DecideResult:
            del rule, config, text
            return DecideResult(outcome="never-produced")

        result = handle_hook_request(
            _request(tmp_path),
            config=_CONFIG,
            decide_fn=decide_returns_out_of_schema_outcome,
        )

        # Even when the outcome matches the orphaned `on:` key exactly
        # (only reachable by defying the outcomes schema), the pipeline
        # still does not crash.
        assert result["exit_code"] == 0
