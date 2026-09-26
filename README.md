# Vaudeville

pydantic-ai-powered semantic hook enforcement for [Claude Code](https://claude.ai/code). Classifies AI assistant output against YAML rules using a configurable LLM provider.

## Get the Hook

![Get the hook](.github/assets/get-the-hook.gif)

In turn-of-the-century vaudeville theatres, a stagehand waited in the wings with a long shepherd's crook. When an act started flailing — forgetting lines, losing the crowd, running past its slot — the manager would signal and the hook would shoot out from the curtain and yank the performer offstage before the audience soured on the whole bill. "Get the hook!" became shorthand for cutting a bad act short.

That's the job here. A model watches what Claude is about to say or do and, when the performance goes off the rails — hedging about untested code, dismissing a test failure as "pre-existing," deferring a reviewer's concern to a follow-up PR, declaring work complete with known gaps — it reaches out from the wings and pulls the act. Unlike regex hooks, the model reads *intent*, so it catches the act whether Claude says "this should work," "I believe this addresses it," or "we can tighten this up later." Bad patterns get yanked; honest uncertainty gets through.

## How It Works

Vaudeville runs a local daemon that classifies Claude Code's output in real time via [pydantic-ai](https://ai.pydantic.dev/), calling whichever model provider you configure. You write rules as YAML files with few-shot prompt templates. The daemon evaluates them on every hook event and returns block/warn/log verdicts.

**Fail-open by design** — if the daemon is down, the config is missing, or a model call errors out, your session continues normally. Vaudeville never blocks you from working.

## Install

```
/plugin marketplace add paulnsorensen/vaudeville
/plugin install vaudeville@paulnsorensen
```

Then run the one-time setup to write your config:

```
/vaudeville:setup
```

This creates `~/.vaudeville/config` with a `default_model`, `providers` (API key env vars), and optional `commands`. Any project rule may invoke any configured command; a command's child process never sees the provider API key env vars.

### Install for Pi

```bash
pi install git:github.com/paulnsorensen/vaudeville
```

Pi loads `pi/extensions/vaudeville.ts` from the package's `pi.extensions` manifest entry (`package.json`). The extension talks to the same daemon over its Unix socket and spawns it on `session_start` via `hooks/session-start.sh`; it does not run `hooks/hooks.json`, so a Pi-only install has exactly one enforcement path.

### Install for oh-my-pi

In an oh-my-pi session:

```text
/marketplace add paulnsorensen/vaudeville
/marketplace install vaudeville@paulnsorensen
```

oh-my-pi reads `.claude-plugin/marketplace.json` and loads the extension from `package.json#omp.extensions`. Its Claude-plugin loader reads only `hooks/pre/` and `hooks/post/`, so it does not run `hooks/hooks.json`. The extension is the one enforcement path.

## Quick Start (5 minutes to first hook)

1. Copy the bundled rules to your global rules directory:
   ```bash
   mkdir -p ~/.vaudeville/rules
   cp ~/.claude/plugins/**/paulnsorensen/vaudeville/**/examples/rules/*.yaml ~/.vaudeville/rules/
   ```

2. Start a new Claude Code session — the daemon launches automatically on `SessionStart`.

3. Try it: ask Claude to make a small change and watch it finish. If it ends with "Should I commit and push?" instead of just doing it, `git-gate` warns. Reply to a PR comment with "I'll address this in a follow-up PR" and `deferral-detector` blocks the comment.

## Uninstall

`/plugin remove vaudeville` removes the plugin files but does not clean up the standalone `vaudeville` CLI shim or the `argcomplete` helper that `/vaudeville:setup` installed into uv's tool bin (often `~/.local/bin`). To remove them:

```bash
uv tool uninstall vaudeville
uv tool uninstall argcomplete
```

If you added the tab-completion activation line to your shell rc (`~/.bashrc`, `~/.zshrc`, or `~/.config/fish/config.fish`), remove it as well — it references `register-python-argcomplete`, which will no longer exist.

To also clear the downloaded model and rules:

```bash
rm -rf ~/.vaudeville
```

## Bundled Rules

The plugin ships a set of example rules in [`examples/rules/`](examples/rules/). **They are examples, not active configuration** — the rule loader reads from `~/.vaudeville/rules/` (global) and `<project>/.vaudeville/rules/`, so nothing fires until you copy the ones you want into one of those directories (see Quick Start step 1).

The bundled examples:

| Rule | Event | What it catches |
|---|---|---|
| `git-gate` | Stop | Asking permission to commit/push/open a PR when work is clearly done ("Should I commit?", "Want me to push?") |
| `deferral-detector` | PreToolUse | PR replies that defer reviewer concerns to "follow-up PRs", tickets, or "next iteration" |

Any rule can be gated with `draft: true` at the top of its YAML — the loader will skip it until that line is removed. Useful while iterating on a new rule without removing it from the directory.

## Custom Rules

Rules live in `~/.vaudeville/rules/` (global) or `.vaudeville/rules/` (per-project). A project rule cannot override a global rule of the same name: the daemon skips it and logs a warning. Give project rules unique names.

See [`examples/rules/`](examples/rules/) for the bundled rules as starting points for your own.

### Authoring Rules

Each rule is a YAML file of `type: decide` or `type: rewrite`. A decide rule sets `name`, `event`, `prompt`, and `outcomes` (the labels the model may return), plus an `on` map from an outcome to an action (`allow`, `log`, `warn`, `block`, `feedback`, `rewrite`, `escalate`, `ask`, `add-context`, `run`). A rewrite rule sets `target` (the `tool_input.*` fields to rewrite) instead of `outcomes`/`on`. Both accept `tier` (`disabled | shadow | log | warn | block`), and optional `matcher` and `model`. The `event` field determines *when* the rule fires during a Claude Code session.

### Choosing an Event

| Event | When it fires | Input available | Use for |
|---|---|---|---|
| `Stop` | After the assistant finishes its final response | `last_assistant_message` | Checking the quality of completed responses — hedging, sycophancy, false completion claims, unresolved findings |
| `PreToolUse` | Before a tool call executes | `tool_name`, `tool_input` | Intercepting dangerous actions or low-quality tool inputs (e.g., PR reply deferrals, unsafe commands) before they happen |
| `PostToolUse` | After a tool call returns | `tool_name`, `tool_input`, `tool_output` | Validating tool results — checking that test output was actually read, verifying file contents match expectations |

**Which event should my rule use?**

- **Reviewing what the assistant said** → `Stop`. Quality rules like `git-gate` use this because they evaluate the assistant's final output.
- **Blocking a bad action before it happens** → `PreToolUse`. The `deferral-detector` uses this to catch low-quality PR replies before they're posted.
- **Checking what a tool returned** → `PostToolUse`. Use this when the rule needs to see both the tool input and its result.

Other events (`SessionStart`, `UserPromptSubmit`, `Notification`, etc.) are available for specialized use cases. See `hooks/hooks.json` for the full list of wired events.

## Observability

Every classification is appended as a JSONL event by the daemon, so you can inspect rule behavior after the fact.

### Commands

```bash
uv run vaudeville stats           # aggregated per-rule totals, pass rate, latency p50/p95, histogram
uv run vaudeville stats --json    # same data as raw JSON
uv run vaudeville watch           # live TUI of rule firings
```

Both commands accept `--log-path` to point at a non-default events file.

### Log Location

- `~/.vaudeville/logs/events.jsonl` — every classification (ts, rule, verdict, confidence, latency_ms, tier, reason, input_snippet, unsure, unsure_below, confidence_missing). `confidence` is null when the model reports none.
- `~/.vaudeville/logs/violations.jsonl` — subset where `verdict == "violation"`
- `~/.vaudeville/logs/config.yaml` — retention settings (auto-created with defaults on first run)

### What `watch` shows (and what it doesn't)

`vaudeville watch` only shows real hook firings — classifications produced by Claude Code sessions running against your daemon. It does **not** show eval classifications: the eval harness tags its requests with `log_event: false`, and the daemon skips writing them to `events.jsonl`. That keeps the watch stream a faithful picture of production hook traffic instead of being drowned in test-case sweeps.

### Retention

Loguru rotates each log file when it reaches `max_size_mb` and deletes rotated siblings older than `retention_days`. Defaults:

```yaml
max_size_mb: 10
retention_days: 7
```

Edit `~/.vaudeville/logs/config.yaml` to change these. Raise `max_size_mb` if you want `stats` to reflect a longer window — `stats` reads only the live `events.jsonl`, not rotated siblings, so once rotation fires only post-rotation data is aggregated.

## Tuning

Edit a rule's prompt and inline `test_cases`, then run `uv run python -m vaudeville.eval --rule <name>`.
Review the failed cases and change one boundary at a time. Use the host's `/loop` command for repeated agent turns when available.


## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) >= 0.4.27 (Python package manager)
- An API key for your configured model provider

## Development

```bash
just install    # install dependencies
just check      # lint + format + typecheck
just test       # run tests
just eval       # evaluate rules against test cases
```

## Troubleshooting

**Daemon not starting?**
Check `~/.vaudeville/config` exists and has a valid `default_model` and provider API key env var set. Run `/vaudeville:setup` again if not.

**Rules not firing?**
Verify rules are in `~/.vaudeville/rules/` and have valid YAML. Check the daemon socket exists: `ls /tmp/vaudeville-*/vaudeville.sock`.

**False positives?**
Tighten the rule's `prompt` with clearer VIOLATION/CLEAN conditions and more balanced examples, or drop its `tier` (e.g. `warn` → `shadow`) until it is retuned. To act on low confidence instead of the outcome alone, add an optional `unsure:` gate (requires an explicit `model: typesafe:*`); see `examples/README.md` for its fields.

## License

[MIT](LICENSE)
