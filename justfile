# Vaudeville development tasks

# Run all quality checks
check: lint typecheck test

# Run tests
test:
    uv run python -m pytest tests/ -q

# Run tests with coverage report
coverage:
    uv run --with pytest-cov python -m pytest tests/ -q \
        --cov=vaudeville --cov-report=term-missing \
        --cov-fail-under=70 \
        --cov-config=pyproject.toml

# Lint and format check
lint:
    uv run ruff check .
    uv run ruff format --check .

# Type check
typecheck:
    uv run mypy vaudeville/

# Format code
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Run eval harness
eval *ARGS:
    uv run python -m vaudeville.eval {{ARGS}}
