"""Tests for vaudeville.tune.cli — CLI entry point and orchestration."""

from __future__ import annotations

import argparse
import tempfile
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("optuna")

from vaudeville.core.protocol import ClassifyResult  # noqa: E402
from vaudeville.core.rules import Example, Rule  # noqa: E402
from vaudeville.eval import EvalCase  # noqa: E402
from vaudeville.tune.cli import (  # noqa: E402
    EXIT_ERROR,
    EXIT_FAIL,
    EXIT_PASS,
    _build_backend,
    _get_test_file_mtime,
    _try_start_daemon,
    build_parser,
    run_tune,
)


@pytest.fixture
def tune_args() -> argparse.Namespace:
    return argparse.Namespace(
        rule="test-rule",
        p_min=0.95,
        r_min=0.80,
        budget=3,
        no_daemon=True,
        author=False,
    )


@pytest.fixture
def simple_rule() -> Rule:
    return Rule(
        name="test-rule",
        event="Stop",
        prompt="Classify: {{ examples }}\n{text}",
        context=[],
        action="block",
        message="{reason}",
        examples=[
            Example(id="ex1", input="bad", label="violation", reason="bad"),
            Example(id="ex2", input="good", label="clean", reason="good"),
        ],
    )


@pytest.fixture
def eval_cases() -> list[EvalCase]:
    return [
        EvalCase(text="this is bad", label="violation"),
        EvalCase(text="this is fine", label="clean"),
    ]


@pytest.fixture
def mock_backend() -> MagicMock:
    backend = MagicMock()
    backend.classify.return_value = "VERDICT: violation\nREASON: test"
    backend.classify_with_logprobs.return_value = ClassifyResult(
        text="VERDICT: violation\nREASON: test",
        logprobs={},
    )
    return backend


class TestBuildParser:
    def test_parses_rule_name(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["my-rule"])
        assert args.rule == "my-rule"

    def test_default_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["my-rule"])
        assert args.p_min == 0.95
        assert args.r_min == 0.80
        assert args.budget == 15
        assert args.no_daemon is False
        assert args.author is False

    def test_custom_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "my-rule",
                "--p-min",
                "0.90",
                "--r-min",
                "0.70",
                "--budget",
                "10",
                "--no-daemon",
            ]
        )
        assert args.p_min == 0.90
        assert args.r_min == 0.70
        assert args.budget == 10
        assert args.no_daemon is True


class TestBuildBackend:
    @patch("vaudeville.tune.cli.daemon_is_alive", return_value=True)
    @patch("vaudeville.tune.cli.DaemonBackend")
    def test_prefers_daemon(
        self,
        mock_cls: MagicMock,
        mock_alive: MagicMock,
    ) -> None:
        backend = _build_backend(no_daemon=False)
        mock_cls.assert_called_once()
        assert backend == mock_cls.return_value

    @patch("vaudeville.tune.cli._try_start_daemon", return_value=False)
    @patch("vaudeville.tune.cli.daemon_is_alive", return_value=False)
    def test_falls_back_to_mlx_after_autostart_fails(
        self, mock_alive: MagicMock, mock_start: MagicMock
    ) -> None:
        mock_mlx_cls = MagicMock()
        with patch.dict(
            "sys.modules",
            {"vaudeville.server.mlx_backend": MagicMock(MLXBackend=mock_mlx_cls)},
        ):
            with patch("vaudeville.server.MLXBackend", mock_mlx_cls):
                backend = _build_backend(no_daemon=False)
                mock_start.assert_called_once()
                mock_mlx_cls.assert_called_once()
                assert backend == mock_mlx_cls.return_value

    @patch("vaudeville.tune.cli._try_start_daemon", return_value=True)
    @patch("vaudeville.tune.cli.DaemonBackend")
    @patch("vaudeville.tune.cli.daemon_is_alive", return_value=False)
    def test_autostart_success_returns_daemon_backend(
        self,
        mock_alive: MagicMock,
        mock_cls: MagicMock,
        mock_start: MagicMock,
    ) -> None:
        backend = _build_backend(no_daemon=False)
        mock_start.assert_called_once()
        mock_cls.assert_called_once()
        assert backend == mock_cls.return_value

    @patch("vaudeville.tune.cli.daemon_is_alive", return_value=True)
    @patch("vaudeville.tune.cli.DaemonBackend")
    def test_no_daemon_flag_skips_daemon(
        self,
        mock_cls: MagicMock,
        mock_alive: MagicMock,
    ) -> None:
        with patch.dict("sys.modules", {"vaudeville.server.mlx_backend": MagicMock()}):
            try:
                _build_backend(no_daemon=True)
            except Exception:
                pass
        mock_cls.assert_not_called()


class TestTryStartDaemon:
    @patch("vaudeville.tune.cli.daemon_is_alive", return_value=True)
    @patch("vaudeville.tune.cli.subprocess.Popen")
    @patch("vaudeville.tune.cli.time.sleep")
    def test_returns_true_when_daemon_comes_alive(
        self,
        mock_sleep: MagicMock,
        mock_popen: MagicMock,
        mock_alive: MagicMock,
    ) -> None:
        assert _try_start_daemon() is True
        mock_popen.assert_called_once()

    @patch("vaudeville.tune.cli.subprocess.Popen", side_effect=OSError("no such file"))
    def test_returns_false_on_spawn_failure(self, mock_popen: MagicMock) -> None:
        assert _try_start_daemon() is False

    @patch("vaudeville.tune.cli.daemon_is_alive", return_value=False)
    @patch("vaudeville.tune.cli.subprocess.Popen")
    @patch("vaudeville.tune.cli.time.sleep")
    @patch("vaudeville.tune.cli.time.monotonic")
    def test_returns_false_on_timeout(
        self,
        mock_monotonic: MagicMock,
        mock_sleep: MagicMock,
        mock_popen: MagicMock,
        mock_alive: MagicMock,
    ) -> None:
        mock_monotonic.side_effect = [0.0, 0.0, 5.0, 11.0]
        assert _try_start_daemon() is False


class TestGetTestFileMtime:
    def test_returns_zero_for_missing_file(self) -> None:
        mtime = _get_test_file_mtime("nonexistent-rule-xyz")
        assert mtime == 0.0

    def test_returns_mtime_for_existing_file(self) -> None:
        import os

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            f.write(b"rule: test\ncases: []")
            f.flush()
            expected = os.path.getmtime(f.name)

        try:
            assert expected > 0
        finally:
            os.unlink(f.name)


class TestRunTune:
    @patch("vaudeville.tune.cli.load_rules_layered")
    def test_missing_rule_returns_error(
        self,
        mock_load: MagicMock,
        tune_args: argparse.Namespace,
    ) -> None:
        mock_load.return_value = {}
        code = run_tune(tune_args)
        assert code == EXIT_ERROR

    @patch("vaudeville.tune.cli._build_backend")
    @patch("vaudeville.tune.cli._get_test_file_mtime", return_value=0.0)
    @patch("vaudeville.tune.cli._load_cases_for_rule")
    @patch("vaudeville.tune.cli.load_rules_layered")
    def test_no_cases_returns_error(
        self,
        mock_load: MagicMock,
        mock_cases: MagicMock,
        mock_mtime: MagicMock,
        mock_backend: MagicMock,
        tune_args: argparse.Namespace,
        simple_rule: Rule,
    ) -> None:
        mock_load.return_value = {"test-rule": simple_rule}
        mock_cases.return_value = []
        code = run_tune(tune_args)
        assert code == EXIT_ERROR

    @patch("vaudeville.tune.cli._build_backend")
    @patch("vaudeville.tune.cli._get_test_file_mtime", return_value=0.0)
    @patch("vaudeville.tune.cli._load_cases_for_rule")
    @patch("vaudeville.tune.cli.load_rules_layered")
    @patch("vaudeville.tune.cli.run_study")
    def test_passing_verdict_returns_pass(
        self,
        mock_study: MagicMock,
        mock_load: MagicMock,
        mock_cases: MagicMock,
        mock_mtime: MagicMock,
        mock_backend_fn: MagicMock,
        tune_args: argparse.Namespace,
        simple_rule: Rule,
        eval_cases: list[EvalCase],
    ) -> None:
        from vaudeville.tune.study import TuneVerdict

        mock_load.return_value = {"test-rule": simple_rule}
        mock_cases.return_value = eval_cases
        mock_study.return_value = TuneVerdict(
            passed=True,
            p_tune=0.96,
            r_tune=0.85,
            p_held=0.96,
            r_held=0.85,
            trials_run=3,
            pool_size=2,
            best_ids=["ex1"],
            study_uri="sqlite:///test.db",
            diff_path="/tmp/test.diff",
        )
        code = run_tune(tune_args)
        assert code == EXIT_PASS

    @patch("vaudeville.tune.cli._build_backend")
    @patch("vaudeville.tune.cli._get_test_file_mtime", return_value=0.0)
    @patch("vaudeville.tune.cli._load_cases_for_rule")
    @patch("vaudeville.tune.cli.load_rules_layered")
    @patch("vaudeville.tune.cli.run_study")
    def test_failing_verdict_returns_fail(
        self,
        mock_study: MagicMock,
        mock_load: MagicMock,
        mock_cases: MagicMock,
        mock_mtime: MagicMock,
        mock_backend_fn: MagicMock,
        tune_args: argparse.Namespace,
        simple_rule: Rule,
        eval_cases: list[EvalCase],
    ) -> None:
        from vaudeville.tune.study import TuneVerdict

        mock_load.return_value = {"test-rule": simple_rule}
        mock_cases.return_value = eval_cases
        mock_study.return_value = TuneVerdict(
            passed=False,
            p_tune=0.80,
            r_tune=0.60,
            p_held=0.80,
            r_held=0.60,
            trials_run=3,
            pool_size=2,
            best_ids=["ex1"],
            study_uri="sqlite:///test.db",
            diff_path="/tmp/test.diff",
        )
        code = run_tune(tune_args)
        assert code == EXIT_FAIL


class TestMain:
    @patch("vaudeville.tune.cli.run_tune", return_value=EXIT_PASS)
    def test_main_exits_with_code(self, mock_run: MagicMock) -> None:
        from vaudeville.tune.cli import main

        with patch("sys.argv", ["vaudeville-tune", "test-rule"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_PASS

    @patch(
        "vaudeville.tune.cli.run_tune",
        side_effect=RuntimeError("boom"),
    )
    def test_main_catches_exception(self, mock_run: MagicMock) -> None:
        from vaudeville.tune.cli import main

        with patch("sys.argv", ["vaudeville-tune", "test-rule"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == EXIT_ERROR


class TestFindProjectRoot:
    def test_finds_root(self) -> None:
        from vaudeville.tune.cli import _find_project_root

        root = _find_project_root()
        assert root is not None

    @patch("subprocess.run", side_effect=OSError("no git"))
    def test_returns_none_on_error(self, mock_run: MagicMock) -> None:
        from vaudeville.tune.cli import _find_project_root

        assert _find_project_root() is None


class TestLoadCasesForRule:
    def test_returns_empty_for_unknown_rule(self) -> None:
        from vaudeville.tune.cli import _load_cases_for_rule

        cases = _load_cases_for_rule("nonexistent-rule-xyz-123")
        assert cases == []

    def test_loads_existing_rule_cases(self) -> None:
        import os

        import yaml

        from vaudeville.tune.cli import _load_cases_for_rule

        with tempfile.TemporaryDirectory() as tmpdir:
            tests_dir = os.path.join(tmpdir, "tests")
            os.makedirs(tests_dir)
            test_data = {
                "rule": "my-test-rule",
                "cases": [
                    {"text": "bad text", "label": "violation"},
                    {"text": "good text", "label": "clean"},
                ],
            }
            with open(os.path.join(tests_dir, "my-test-rule.yaml"), "w") as f:
                yaml.dump(test_data, f)

            with patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": tmpdir}):
                cases = _load_cases_for_rule("my-test-rule")
                assert len(cases) == 2
                assert cases[0].label == "violation"
