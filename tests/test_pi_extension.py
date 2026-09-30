"""Run native extension regressions through the project test gate."""

import subprocess
from pathlib import Path


def test_pi_extension_behavior() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--test", "tests/pi_extension.test.mjs"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
