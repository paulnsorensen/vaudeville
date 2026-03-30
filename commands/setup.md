---
description: Install prerequisites and download the SLM model for vaudeville
allowed-tools:
  - Bash
---

# Vaudeville Setup

Run the full setup sequence: install `uv` if missing, sync Python dependencies, and download the inference model.

## Steps

1. **Check for `uv`** — if not found, install it via the official installer:

```bash
if ! command -v uv &>/dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Reload PATH so uv is available immediately
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "uv $(uv --version)"
```

2. **Sync dependencies** — install the correct backend for this architecture:

```bash
arch=$(uname -m)
if [ "$arch" = "arm64" ]; then
  echo "Apple Silicon detected — syncing mlx backend..."
  uv sync --project "${CLAUDE_PLUGIN_ROOT}" --group dev --group mlx
else
  echo "x86_64 detected — syncing gguf backend..."
  uv sync --project "${CLAUDE_PLUGIN_ROOT}" --group dev --group gguf
fi
```

3. **Download the model** (~2.4 GB, one-time):

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m vaudeville.setup
```

4. **Verify the daemon starts** — restart the session or run the session-start hook manually:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh" < /dev/null
```

Report success or failure to the user after each step. If any step fails, stop and diagnose before continuing.
