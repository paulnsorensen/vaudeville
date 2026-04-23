#!/usr/bin/env bash
# Pre-flight check for the gguf source build on Linux aarch64.
#
# llama-cpp-python >= 0.3.4 does not publish a manylinux wheel for Linux
# aarch64, so `uv sync --group gguf` falls back to a source build that
# silently invokes cmake and a C/C++ compiler.  If either is absent the
# build stalls for minutes before emitting a deep cmake traceback.
#
# This script is called from both `just install`, `just setup`, and
# commands/setup.md step 2 *before* the uv sync so users get actionable
# guidance immediately instead of waiting.
#
# Exit codes:
#   0 — platform is not Linux/aarch64, or all required tools are present
#   1 — Linux/aarch64 and one or more required tools are missing

set -euo pipefail

os=$(uname -s)
arch=$(uname -m)

# Only applies to Linux aarch64 — every other platform gets a binary wheel.
if [[ "$os" != "Linux" || "$arch" != "aarch64" ]]; then
    exit 0
fi

missing=()

if ! command -v cmake &>/dev/null; then
    missing+=("cmake")
fi

if ! command -v cc &>/dev/null && ! command -v gcc &>/dev/null && ! command -v clang &>/dev/null; then
    missing+=("cc (C/C++ compiler: gcc or clang)")
fi

if [[ ${#missing[@]} -eq 0 ]]; then
    echo "gguf pre-flight: cmake and C compiler found — source build will proceed (~5–10 min on aarch64)"
    exit 0
fi

{
    echo "ERROR: Linux aarch64 requires build tools for the gguf source build, but the following are missing:"
    for tool in "${missing[@]}"; do
        echo "  • $tool"
    done
    echo ""
    echo "Install them and re-run:"
    echo ""
    if command -v apt-get &>/dev/null; then
        echo "  Ubuntu/Debian:   sudo apt install build-essential cmake"
    elif command -v dnf &>/dev/null; then
        echo "  RHEL/Fedora:     sudo dnf install gcc-c++ cmake"
    elif command -v yum &>/dev/null; then
        echo "  CentOS/RHEL:     sudo yum install gcc-c++ cmake"
    elif command -v apk &>/dev/null; then
        echo "  Alpine:          apk add build-base cmake"
    else
        echo "  Install cmake and a C/C++ compiler (gcc or clang) for your distro."
    fi
    echo ""
    echo "Note: the gguf source build takes ~5–10 minutes on aarch64 — this is expected once tools are present."
} >&2

exit 1
