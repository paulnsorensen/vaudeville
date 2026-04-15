"""Tuning harness for vaudeville rules via Optuna.

Optuna-dependent symbols (format_verdict, run_study, run_tune,
StudyConfig, TrialContext, TuneVerdict) are lazy-loaded so that
importing lightweight submodules (split, pool) doesn't require
the ``tune`` dependency group.
"""

from .split import split_cases

__all__ = [
    "StudyConfig",
    "TrialContext",
    "TuneVerdict",
    "format_verdict",
    "run_study",
    "run_tune",
    "split_cases",
]

_STUDY_ATTRS = {"StudyConfig", "TrialContext", "TuneVerdict"}
_HARNESS_ATTRS = {"format_verdict", "run_study"}


def __getattr__(name: str) -> object:
    if name in _STUDY_ATTRS:
        from .study import StudyConfig, TrialContext, TuneVerdict

        globals().update(
            StudyConfig=StudyConfig,
            TrialContext=TrialContext,
            TuneVerdict=TuneVerdict,
        )
        return globals()[name]
    if name in _HARNESS_ATTRS:
        from .harness import format_verdict, run_study

        globals().update(format_verdict=format_verdict, run_study=run_study)
        return globals()[name]
    if name == "run_tune":
        from .cli import run_tune

        globals()["run_tune"] = run_tune
        return run_tune
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
