set dotenv-load := true

# Show available commands
@default:
    just --list

# Install dependencies (sync with pyproject.toml, arch-aware backend)
install:
    #!/usr/bin/env bash
    set -euo pipefail
    arch=$(uname -m)
    case "$arch" in
        arm64|aarch64)
            echo "ARM64 detected — installing with mlx backend"
            uv sync --group dev --group mlx
            ;;
        x86_64|amd64)
            echo "x86_64 detected — installing with gguf backend"
            uv sync --group dev --group gguf
            ;;
        *)
            echo "Warning: unknown architecture '$arch' — defaulting to gguf backend" >&2
            uv sync --group dev --group gguf
            ;;
    esac

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

# Download the inference model (one-time setup, ~2.4 GB, arch-aware backend)
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    arch=$(uname -m)
    case "$arch" in
        arm64|aarch64)
            echo "ARM64 detected — syncing mlx backend"
            uv sync --group mlx
            ;;
        *)
            echo "Non-ARM detected — syncing gguf backend"
            uv sync --group gguf
            ;;
    esac
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
    uv run python -m vaudeville.eval --calibrate {{rule}}

# Clean build and test artifacts
clean:
    rm -rf .pytest_cache .mypy_cache .ruff_cache
    find . -type d -name __pycache__ -exec rm -rf {} + || true
    find . -type f -name "*.pyc" -delete || true

# Run a development shell with dependencies
dev:
    uv run python -c "import sys; print(f'Python {sys.version}')" && bash
