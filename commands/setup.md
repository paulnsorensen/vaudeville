---
description: Install prerequisites and download the SLM model for vaudeville
allowed-tools:
  - Bash
---

# Vaudeville Setup

Run the full setup sequence: install `uv` if missing, sync Python dependencies, and download the inference model.

Each step below is a self-contained bash block. `~/.local/bin` is prepended to `PATH` at the top of each block so a freshly-installed `uv` (or `uv tool`-installed binary) is visible even if the user's shell rc hasn't been reloaded — Claude Code runs each block in a separate subshell, so `export PATH=...` does not persist across steps.

## Steps

1. **Check for `uv`** — if not found, install it via the official installer. `set -o pipefail` ensures a silent `curl` failure does not masquerade as a successful install:

```bash
set -o pipefail
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv &>/dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || {
    echo "uv install failed — check network / TLS and re-run /vaudeville:setup" >&2
    exit 1
  }
fi
echo "uv $(uv --version)"
```

2. **Sync dependencies** — install the correct backend for this OS+arch. MLX is Apple Silicon only; everything else (incl. Linux aarch64) uses gguf:

```bash
export PATH="$HOME/.local/bin:$PATH"
os=$(uname -s)
arch=$(uname -m)
if [[ "$os" == "Darwin" && "$arch" == "arm64" ]]; then
  echo "Apple Silicon detected — syncing mlx backend..."
  uv sync --project "${CLAUDE_PLUGIN_ROOT}" --group dev --group mlx
else
  echo "${os}/${arch} detected — syncing gguf backend..."
  uv sync --project "${CLAUDE_PLUGIN_ROOT}" --group dev --group gguf
fi
```

3. **Expose the `vaudeville` CLI on PATH** — editable install against the plugin root so `git pull` updates take effect immediately, with `--force` to handle plugin path changes on re-runs:

```bash
export PATH="$HOME/.local/bin:$PATH"
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
export PATH="$HOME/.local/bin:$PATH"
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
export PATH="$HOME/.local/bin:$PATH"
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m vaudeville setup
```

5. **Verify the daemon starts** — restart the session or run the session-start hook manually:

```bash
export PATH="$HOME/.local/bin:$PATH"
bash "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh" < /dev/null
```

Report success or failure to the user after each step. If any step fails, stop and diagnose before continuing.
