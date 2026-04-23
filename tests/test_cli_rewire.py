"""Tests for __main__ scope flag and CLI rewiring."""

from __future__ import annotations

import argparse
from argparse import Namespace
from unittest.mock import patch

import pytest


class TestScopeFlag:
    """Test --scope flag parsing and rules_dir resolution."""

    def test_scope_default_is_global(self) -> None:
        """Default scope is 'global' when not specified."""
        from vaudeville.__main__ import _build_tune_parser

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        _build_tune_parser(sub)
        args = parser.parse_args(["tune", "myrule"])

        assert args.scope == "global"

    def test_scope_global_resolves_to_home_rules(self) -> None:
        """scope='global' resolves to ~/.vaudeville/rules."""
        from vaudeville.__main__ import _resolve_rules_dir

        with patch(
            "os.path.expanduser",
            side_effect=lambda p: "/home/user" if p == "~" else p,
        ):
            rules_dir = _resolve_rules_dir("global", None)

        assert rules_dir == "/home/user/.vaudeville/rules"

    def test_scope_project_without_git_root_exits_2(self) -> None:
        """scope='project' with no git root exits with code 2."""
        from vaudeville.__main__ import cmd_tune

        args = Namespace(
            rule="r",
            p_min=0.95,
            r_min=0.80,
            f1_min=0.85,
            rounds=1,
            tuner_iters=5,
            scope="project",
        )

        with patch("vaudeville.__main__._strict_project_root", return_value=None):
            with patch("vaudeville.__main__._find_commands_dir", return_value="/cmd"):
                with pytest.raises(SystemExit) as exc_info:
                    cmd_tune(args)

        assert exc_info.value.code == 2

    def test_scope_project_resolves_to_project_rules(self) -> None:
        """scope='project' with a git root resolves to project/.vaudeville/rules."""
        from vaudeville.__main__ import _resolve_rules_dir

        rules_dir = _resolve_rules_dir("project", "/tmp/proj")

        assert rules_dir == "/tmp/proj/.vaudeville/rules"


class TestCmdRewire:
    """Test __main__.py cmd_tune and cmd_generate call the orchestrator correctly."""

    def test_cmd_tune_forwards_rounds_and_tuner_iters(self) -> None:
        """cmd_tune passes --rounds and --tuner-iters to orchestrator."""
        from vaudeville.__main__ import cmd_tune

        with patch("vaudeville.orchestrator.orchestrate_tune") as mock_orch:
            mock_orch.return_value = 0

            args = Namespace(
                rule="test-rule",
                p_min=0.95,
                r_min=0.80,
                f1_min=0.85,
                rounds=2,
                tuner_iters=10,
                scope="global",
            )

            with patch(
                "vaudeville.__main__._strict_project_root", return_value="/proj"
            ):
                with patch(
                    "vaudeville.__main__._find_commands_dir",
                    return_value="/proj/commands",
                ):
                    cmd_tune(args)

        mock_orch.assert_called_once()
        call_kwargs = mock_orch.call_args[1]
        assert call_kwargs["rounds"] == 2
        assert call_kwargs["tuner_iters"] == 10

    def test_cmd_generate_forwards_mode(self) -> None:
        """cmd_generate forwards --live flag as mode to orchestrator."""
        from vaudeville.__main__ import cmd_generate

        with patch("vaudeville.orchestrator.orchestrate_generate") as mock_orch:
            mock_orch.return_value = 0

            args = Namespace(
                instructions="test instructions",
                p_min=0.95,
                r_min=0.80,
                f1_min=0.85,
                live=True,
                rounds=3,
                tuner_iters=10,
                scope="global",
            )

            with patch(
                "vaudeville.__main__._strict_project_root", return_value="/proj"
            ):
                with patch(
                    "vaudeville.__main__._find_commands_dir",
                    return_value="/proj/commands",
                ):
                    cmd_generate(args)

        mock_orch.assert_called_once()
        call_kwargs = mock_orch.call_args[1]
        assert call_kwargs["mode"] == "live"
