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
case "$arch" in
  arm64|aarch64)
    echo "ARM64 detected — syncing mlx backend..."
    uv sync --project "${CLAUDE_PLUGIN_ROOT}" --group dev --group mlx
    ;;
  x86_64|amd64)
    echo "x86_64 detected — syncing gguf backend..."
    uv sync --project "${CLAUDE_PLUGIN_ROOT}" --group dev --group gguf
    ;;
  *)
    echo "Unknown architecture '$arch' — defaulting to gguf backend..."
    uv sync --project "${CLAUDE_PLUGIN_ROOT}" --group dev --group gguf
    ;;
esac
```

3. **Expose the `vaudeville` CLI on PATH** — editable install against the plugin root so `git pull` updates take effect immediately, with `--force` to handle plugin path changes on re-runs:

```bash
uv tool install --force --editable "${CLAUDE_PLUGIN_ROOT}"
# Ensure uv's tool bin (~/.local/bin by default) is on PATH; idempotent.
uv tool update-shell

# Install argcomplete separately so `register-python-argcomplete` is on PATH
# for the tab-completion activation line below. argcomplete is already a
# runtime dep inside vaudeville's venv; this just exposes its helper script.
uv tool install --force argcomplete
```

The `vaudeville` tool install only pulls core deps (argcomplete, loguru, pyyaml, rich). The backend (mlx/gguf) stays in the plugin's `uv sync` venv because only `vaudeville setup` needs it.

**Activate tab completion** — print the shell-specific one-liner for the user to add to their shell rc (do not modify their rc automatically):

```bash
shell_name="$(basename "${SHELL:-}")"
case "$shell_name" in
  bash)
    cat <<'MSG'
Add this line to ~/.bashrc to enable tab completion for `vaudeville`:

  eval "$(register-python-argcomplete vaudeville)"
MSG
    ;;
  zsh)
    cat <<'MSG'
Add these lines to ~/.zshrc to enable tab completion for `vaudeville`:

  autoload -U +X bashcompinit && bashcompinit
  eval "$(register-python-argcomplete vaudeville)"
MSG
    ;;
  fish)
    cat <<'MSG'
Add this line to ~/.config/fish/config.fish to enable tab completion for `vaudeville`:

  register-python-argcomplete --shell fish vaudeville | source
MSG
    ;;
  *)
    echo "Unrecognized shell '$shell_name' — see https://kislyuk.github.io/argcomplete/#activating-global-completion for activation."
    ;;
esac
```

4. **Download the model** (~2.4 GB, one-time) — routed through the plugin venv that has the backend deps:

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m vaudeville setup
```

5. **Verify the daemon starts** — restart the session or run the session-start hook manually:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh" < /dev/null
```

Report success or failure to the user after each step. If any step fails, stop and diagnose before continuing.
