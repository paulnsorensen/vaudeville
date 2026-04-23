"""Tests for scripts/gguf-preflight.sh — Linux aarch64 pre-flight check.

The script is exercised as a subprocess with a custom PATH containing mock
executables that shadow real system tools, so the tests are fully hermetic.
A mock ``uname`` binary in the same bin dir controls the perceived OS/arch.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "scripts" / "gguf-preflight.sh"
BASH = shutil.which("bash") or "/bin/bash"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bin(tmp: Path, tools: list[str]) -> Path:
    """Create an isolated bin directory containing stub executables.

    Each stub is a one-line shell script that exits 0 — enough to satisfy
    ``command -v <tool>`` checks in the pre-flight script.
    """
    bin_dir = tmp / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        stub = bin_dir / tool
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(
            stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
        )
    return bin_dir


def _add_uname(bin_dir: Path, system: str, machine: str) -> None:
    """Write a mock ``uname`` into *bin_dir* that reports *system*/*machine*."""
    uname = bin_dir / "uname"
    uname.write_text(
        f"#!/bin/sh\n"
        f'case "$1" in\n'
        f'  -s) echo "{system}" ;;\n'
        f'  -m) echo "{machine}" ;;\n'
        f"  *)  exit 1 ;;\n"
        f"esac\n"
    )
    uname.chmod(
        stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )


def _run(bin_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run the pre-flight script with *bin_dir* as the sole PATH entry."""
    return subprocess.run(
        [BASH, str(SCRIPT)],
        env={"PATH": str(bin_dir)},
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Tests — non-Linux / non-aarch64 platforms (fast-exit)
# ---------------------------------------------------------------------------


class TestNonTargetPlatform:
    def test_darwin_arm64_exits_0(self, tmp_path: Path) -> None:
        """macOS Apple Silicon: script exits 0 immediately — no checks needed."""
        bin_dir = _make_bin(tmp_path, [])
        _add_uname(bin_dir, "Darwin", "arm64")
        result = _run(bin_dir)
        assert result.returncode == 0

    def test_linux_x86_64_exits_0(self, tmp_path: Path) -> None:
        """Linux x86_64: binary wheel is available, no pre-flight needed."""
        bin_dir = _make_bin(tmp_path, [])
        _add_uname(bin_dir, "Linux", "x86_64")
        result = _run(bin_dir)
        assert result.returncode == 0

    def test_darwin_x86_64_exits_0(self, tmp_path: Path) -> None:
        """macOS Intel: not Linux aarch64, so script exits 0."""
        bin_dir = _make_bin(tmp_path, [])
        _add_uname(bin_dir, "Darwin", "x86_64")
        result = _run(bin_dir)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Tests — Linux aarch64, tools present
# ---------------------------------------------------------------------------


class TestLinuxAarch64ToolsPresent:
    def test_cmake_and_cc_exits_0(self, tmp_path: Path) -> None:
        """All required tools present → exit 0."""
        bin_dir = _make_bin(tmp_path, ["cmake", "cc"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 0

    def test_cmake_and_gcc_exits_0(self, tmp_path: Path) -> None:
        """gcc satisfies the C-compiler requirement when cc is absent."""
        bin_dir = _make_bin(tmp_path, ["cmake", "gcc"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 0

    def test_cmake_and_clang_exits_0(self, tmp_path: Path) -> None:
        """clang satisfies the C-compiler requirement when cc/gcc are absent."""
        bin_dir = _make_bin(tmp_path, ["cmake", "clang"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 0

    def test_success_mentions_aarch64_and_build_time(self, tmp_path: Path) -> None:
        """Success output mentions the expected source-build duration."""
        bin_dir = _make_bin(tmp_path, ["cmake", "cc"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 0
        assert "aarch64" in result.stdout
        assert "5" in result.stdout and "10" in result.stdout  # "~5–10 min"


# ---------------------------------------------------------------------------
# Tests — Linux aarch64, tools missing
# ---------------------------------------------------------------------------


class TestLinuxAarch64ToolsMissing:
    def test_cmake_absent_exits_1(self, tmp_path: Path) -> None:
        """cc present but cmake absent → exit 1 mentioning cmake."""
        bin_dir = _make_bin(tmp_path, ["cc"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert "cmake" in result.stderr

    def test_compiler_absent_exits_1(self, tmp_path: Path) -> None:
        """cmake present but no cc/gcc/clang → exit 1 mentioning compiler."""
        bin_dir = _make_bin(tmp_path, ["cmake"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert "compiler" in result.stderr.lower() or "cc" in result.stderr.lower()

    def test_both_absent_exits_1(self, tmp_path: Path) -> None:
        """Both cmake and compiler absent → exit 1 mentioning both."""
        bin_dir = _make_bin(tmp_path, [])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert "cmake" in result.stderr

    def test_error_output_mentions_build_time(self, tmp_path: Path) -> None:
        """Error message includes the expected build-time note."""
        bin_dir = _make_bin(tmp_path, [])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert "5" in result.stderr and "10" in result.stderr  # "~5–10 minutes"

    def test_error_includes_install_command(self, tmp_path: Path) -> None:
        """Error message includes a re-run / install hint."""
        bin_dir = _make_bin(tmp_path, [])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert "install" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Tests — Linux aarch64, package-manager guidance
# ---------------------------------------------------------------------------


class TestPackageManagerGuidance:
    def test_apt_get_guidance(self, tmp_path: Path) -> None:
        """apt-get available → guidance uses apt."""
        bin_dir = _make_bin(tmp_path, ["apt-get"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert "apt" in result.stderr

    def test_dnf_guidance(self, tmp_path: Path) -> None:
        """dnf available (and apt-get absent) → guidance uses dnf."""
        bin_dir = _make_bin(tmp_path, ["dnf"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert "dnf" in result.stderr

    def test_yum_guidance(self, tmp_path: Path) -> None:
        """yum available (apt-get/dnf absent) → guidance uses yum."""
        bin_dir = _make_bin(tmp_path, ["yum"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert "yum" in result.stderr

    def test_apk_guidance(self, tmp_path: Path) -> None:
        """apk available (others absent) → guidance uses apk."""
        bin_dir = _make_bin(tmp_path, ["apk"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert "apk" in result.stderr

    def test_no_package_manager_fallback(self, tmp_path: Path) -> None:
        """No known package manager → generic guidance is shown."""
        bin_dir = _make_bin(tmp_path, [])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert (
            "gcc" in result.stderr
            or "clang" in result.stderr
            or "cmake" in result.stderr
        )

    def test_apt_takes_priority_over_dnf(self, tmp_path: Path) -> None:
        """When both apt-get and dnf are present, apt-get guidance is shown."""
        bin_dir = _make_bin(tmp_path, ["apt-get", "dnf"])
        _add_uname(bin_dir, "Linux", "aarch64")
        result = _run(bin_dir)
        assert result.returncode == 1
        assert "apt" in result.stderr
