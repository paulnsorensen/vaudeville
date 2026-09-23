---
description: Install prerequisites and sync dependencies for vaudeville
allowed-tools:
  - Bash
---

# Vaudeville Setup

Run the full setup sequence: install `uv` if missing, sync Python dependencies, and write the provider config.

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

# Enforce minimum uv version (0.4.27 introduced --group support)
_uv_min="0.4.27"
_uv_actual=$(uv --version | awk '{print $2}')
if [ "$(printf '%s\n' "$_uv_min" "$_uv_actual" | sort -V | head -n1)" != "$_uv_min" ]; then
  echo "ERROR: uv ${_uv_actual} is too old — vaudeville requires uv >= ${_uv_min}." >&2
  echo "Upgrade with: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi
```

2. **Sync dependencies**:

```bash
export PATH="$HOME/.local/bin:$PATH"
uv sync --project "${CLAUDE_PLUGIN_ROOT}"
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

The `vaudeville` tool install only pulls core deps (argcomplete, loguru, pyyaml, rich, pydantic, pydantic-ai-slim). Model access comes from your own provider API key, configured in the `~/.vaudeville/config` step below.

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

4. **Write `~/.vaudeville/config`** — set `default_model` and the provider's API key env var (skipped if the file already exists):

```bash
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$HOME/.vaudeville"
if [ ! -f "$HOME/.vaudeville/config" ]; then
  cat > "$HOME/.vaudeville/config" <<'CONFIG'
default_model: anthropic:claude-haiku-4-5
providers:
  anthropic:
    key_env: ANTHROPIC_API_KEY
commands: {}
CONFIG
  echo "Wrote $HOME/.vaudeville/config — edit default_model/providers to match your setup."
else
  echo "$HOME/.vaudeville/config already exists — leaving it as is."
fi
```

5. **Verify the daemon starts** — restart the session or run the session-start hook manually:

```bash
export PATH="$HOME/.local/bin:$PATH"
bash "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh" < /dev/null
```

Report success or failure to the user after each step. If any step fails, stop and diagnose before continuing.
