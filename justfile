set dotenv-load := true

# Show available commands
@default:
    just --list

# Install dependencies (MLX on Apple Silicon, gguf everywhere else incl. Linux aarch64)
install:
    #!/usr/bin/env bash
    set -euo pipefail
    os=$(uname -s)
    arch=$(uname -m)
    if [[ "$os" == "Darwin" && "$arch" == "arm64" ]]; then
        echo "Apple Silicon detected — installing with mlx backend"
        uv sync --group dev --group mlx
    else
        echo "${os}/${arch} detected — installing with gguf backend"
        bash "{{justfile_directory()}}/scripts/gguf-preflight.sh"
        uv sync --group dev --group gguf
    fi

# Run the full test suite
test *args:
    uv run pytest {{args}}

# Run tests with coverage report (fails under 70% floor)
coverage *args:
    uv run --with pytest-cov pytest \
        --cov=vaudeville --cov-report=term-missing \
        --cov-fail-under=70 \
        --cov-config=pyproject.toml {{args}}

# Run all quality checks (format, lint, type)
check: fmt-check lint type-check
    @echo "All checks passed ✓"

# Full validation pipeline (rtk-wrapped). Agents MUST run this before completion.
build:
    #!/usr/bin/env bash
    set -euo pipefail
    base="${VAUDEVILLE_BUILD_BASE:-origin/main}"
    if ! git rev-parse --verify "$base" >/dev/null 2>&1; then
        base="main"
    fi
    echo "→ autoformat (ruff format)"
    uv run rtk ruff format .
    echo "→ lint with autofix (ruff check --fix)"
    uv run rtk ruff check --fix .
    echo "→ typecheck (mypy --strict)"
    uv run rtk mypy --strict vaudeville/ tests/
    echo "→ tests + coverage (xml + term)"
    uv run rtk pytest \
        --cov=vaudeville \
        --cov-config=pyproject.toml \
        --cov-report=xml:coverage.xml \
        --cov-fail-under=90 || {
        echo "→ coverage failed — per-file breakdown:"
        uv run coverage report --show-missing
        exit 1
    }
    echo "→ enforce 90% line coverage on new lines vs ${base}"
    uv run diff-cover coverage.xml \
        --compare-branch="${base}" \
        --fail-under=95 || {
        echo "→ diff-cover failed — details above."
        exit 1
    }
    echo "build ✓"

# Run ruff linter
lint:
    uv run ruff check .

# Format code with ruff
fmt:
    uv run ruff format .

# Verify format compliance (used in CI)
fmt-check:
    uv run ruff format --check .

# Run mypy type checker (strict mode)
type-check:
    uv run mypy --strict vaudeville/ tests/

# Install `vaudeville` CLI to ~/.local/bin via uv tool (no shell rc modifications)
install-cli:
    #!/usr/bin/env bash
    set -euo pipefail
    os=$(uname -s)
    arch=$(uname -m)
    if [[ "$os" == "Darwin" && "$arch" == "arm64" ]]; then
        backend="mlx"
        with_args=(--with "mlx-lm>=0.31.0,<0.32")
    else
        backend="gguf"
        with_args=(--with "llama-cpp-python>=0.3.4" --with "huggingface-hub>=0.24.0")
    fi
    echo "Installing vaudeville CLI (backend: $backend)..."
    uv tool install --force "${with_args[@]}" .
    bin_dir="${HOME}/.local/bin"
    if [[ ":${PATH}:" == *":${bin_dir}:"* ]]; then
        echo "✓ vaudeville installed. ${bin_dir} is already on PATH."
        exit 0
    fi
    echo ""
    echo "⚠  ${bin_dir} is NOT on your PATH."
    echo "   Add it to your shell rc yourself (we don't touch dotfiles):"
    echo ""
    shell_name=$(basename "${SHELL:-}")
    case "$shell_name" in
        zsh)  echo "     echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;;
        bash) echo "     echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc" ;;
        fish) echo "     fish_add_path ~/.local/bin" ;;
        *)    echo "     export PATH=\"\$HOME/.local/bin:\$PATH\"   # add to your shell rc" ;;
    esac
    echo ""
    echo "   Or run \`uv tool update-shell\` to let uv patch it for you."

# One-time model download (~2.4 GB; MLX on Apple Silicon, gguf everywhere else)
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    os=$(uname -s)
    arch=$(uname -m)
    if [[ "$os" == "Darwin" && "$arch" == "arm64" ]]; then
        echo "Apple Silicon detected — syncing mlx backend"
        uv sync --group mlx
    else
        echo "${os}/${arch} detected — syncing gguf backend"
        bash "{{justfile_directory()}}/scripts/gguf-preflight.sh"
        uv sync --group gguf
    fi
    uv run python -m vaudeville.setup

# Run the eval harness on all rules
eval:
    uv run python -m vaudeville.eval

# Run eval with cross-validation
eval-cv:
    uv run python -m vaudeville.eval --cross-validate

# Run eval for a specific rule (e.g., `just eval-rule violation-detector`)
eval-rule rule:
    uv run python -m vaudeville.eval --rule {{rule}}

# Calibrate threshold for a rule (e.g., `just eval-calibrate violation-detector`)
eval-calibrate rule:
    VAUDEVILLE_SKIP=1 uv run python -m vaudeville.eval --calibrate {{rule}}

# Clean build and test artifacts
clean:
    rm -rf .pytest_cache .mypy_cache .ruff_cache
    find . -type d -name __pycache__ -exec rm -rf {} + || true
    find . -type f -name "*.pyc" -delete || true

# Run a development shell with dependencies
dev:
    uv run python -c "import sys; print(f'Python {sys.version}')" && bash
