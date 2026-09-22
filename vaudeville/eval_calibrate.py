"""Threshold calibration for vaudeville rules.

Threshold-based calibration was tied to the old logprob-scoring backend:
the new `decide` agent picks a typed outcome directly instead of
thresholding a logprob-derived
confidence score. FU-1b (pydantic-evals) replaces this with proper eval
calibration; `--calibrate` stays accepted so existing scripts do not break,
but it now only prints a deferral notice.
"""

from __future__ import annotations


def run_calibrate(rule_name: str) -> None:
    """Print the FU-1b deferral notice for `--calibrate RULE_NAME`."""
    print(
        f"vaudeville: --calibrate {rule_name} is deferred to FU-1b "
        "(pydantic-evals); no threshold sweep runs"
    )
