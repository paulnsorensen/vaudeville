# Vaudeville

SLM-powered semantic hook enforcement for [Claude Code](https://claude.ai/code). Classifies AI assistant output against YAML rules using local Phi-4-mini inference.

## Get the Hook

![Get the hook](.github/assets/get-the-hook.gif)

In turn-of-the-century vaudeville theatres, a stagehand waited in the wings with a long shepherd's crook. When an act started flailing — forgetting lines, losing the crowd, running past its slot — the manager would signal and the hook would shoot out from the curtain and yank the performer offstage before the audience soured on the whole bill. "Get the hook!" became shorthand for cutting a bad act short.

That's the job here. A local small language model watches what Claude is about to say or do and, when the performance goes off the rails — hedging about untested code, dismissing a test failure as "pre-existing," deferring a reviewer's concern to a follow-up PR, declaring work complete with known gaps — it reaches out from the wings and pulls the act. Unlike regex hooks, the SLM reads *intent*, so it catches the act whether Claude says "this should work," "I believe this addresses it," or "we can tighten this up later." Bad patterns get yanked; honest uncertainty gets through.

## How It Works

Vaudeville runs a local inference daemon (Phi-4-mini, 3.8B params, int4) that classifies Claude Code's output in real time. You write rules as YAML files with few-shot prompt templates. The daemon evaluates them on every hook event and returns block/warn/log verdicts.

**Apple Silicon is the first-class target.** On Macs the daemon uses the MLX backend, running Phi-4-mini on the GPU via unified memory (~200ms per classification). On x86_64 the daemon falls back to the GGUF backend (llama-cpp-python, CPU only) — same Phi-4-mini weights, but expect noticeably higher latency. Other platforms/models aren't supported; both backends hard-code the Phi-4-mini repos.

**Fail-open by design** — if the daemon is down, the model isn't downloaded, or inference errors out, your session continues normally. Vaudeville never blocks you from working.

## Install

```
/plugin marketplace add paulnsorensen/vaudeville
/plugin install vaudeville@paulnsorensen
```

Then run the one-time setup to download the model (~2.4 GB):

```
/vaudeville:setup
```

This detects your platform (Apple Silicon or x86_64) and downloads the appropriate model variant.

## Quick Start (5 minutes to first hook)

1. Copy the bundled rules to your global rules directory:
   ```bash
   mkdir -p ~/.vaudeville/rules
   cp ~/.claude/plugins/cache/paulnsorensen/vaudeville/*/examples/rules/*.yaml ~/.vaudeville/rules/
   ```

2. Start a new Claude Code session — the daemon launches automatically on `SessionStart`.

3. Try it: ask Claude to explain something. If the response opens with hedging ("this should work") or dismisses a test failure, the rule fires and you'll see a warning or block.

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
| `violation-detector` | Stop | Hedging, TODOs, unresolved findings in final responses |
| `sycophancy-detector` | Stop | Unearned praise or flattery openers ("Great question!") |
| `turn-waste-detector` | Stop | Responses that narrate a failure journey without a working solution |
| `over-asking-detector` | Stop | Stalling on trivial permission requests instead of proceeding |
| `preexisting-fix-detector` | Stop | Dismissing failures as "pre-existing" without evidence |
| `deferral-detector` | PreToolUse | PR replies that defer reviewer concerns to "follow-up PRs" |

Any rule can be gated with `draft: true` at the top of its YAML — the loader will skip it until that line is removed. Useful while iterating on a new rule without removing it from the directory.

## Custom Rules

Rules live in `~/.vaudeville/rules/` (global) or `.vaudeville/rules/` (per-project, higher priority). Per-project rules take priority over global ones.

See [`examples/rules/`](examples/rules/) for the bundled rules as starting points for your own.

### Authoring Rules

Each rule is a YAML file with a `name`, `event`, `prompt`, `labels`, `action`, and `threshold`. The `event` field determines *when* the rule fires during a Claude Code session.

### Choosing an Event

| Event | When it fires | Input available | Use for |
|---|---|---|---|
| `Stop` | After the assistant finishes its final response | `last_assistant_message` | Checking the quality of completed responses — hedging, sycophancy, false completion claims, unresolved findings |
| `PreToolUse` | Before a tool call executes | `tool_name`, `tool_input` | Intercepting dangerous actions or low-quality tool inputs (e.g., PR reply deferrals, unsafe commands) before they happen |
| `PostToolUse` | After a tool call returns | `tool_name`, `tool_input`, `tool_output` | Validating tool results — checking that test output was actually read, verifying file contents match expectations |

**Which event should my rule use?**

- **Reviewing what the assistant said** → `Stop`. Most quality rules (violation-detector, sycophancy-detector, turn-waste-detector) use this because they evaluate the assistant's final output.
- **Blocking a bad action before it happens** → `PreToolUse`. The deferral-detector uses this to catch low-quality PR replies before they're posted.
- **Checking what a tool returned** → `PostToolUse`. Use this when the rule needs to see both the tool input and its result.

Other events (`SessionStart`, `UserPromptSubmit`, `Notification`, etc.) are available for specialized use cases. See `hooks/hooks.json` for the full list of wired events.

### Backend Differences

The GGUF backend (llama-cpp-python) enforces the `VERDICT: .../REASON: ...` output format via GBNF grammar-constrained decoding, guaranteeing structurally valid responses. MLX-LM doesn't support GBNF grammars, so the MLX backend relies on a system prompt plus a newline-count stop condition (halts after two newlines once the VERDICT and REASON lines are emitted). Both backends use temperature 0.0 for deterministic inference; the GGUF backend additionally applies `repeat_penalty=1.1`.

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

- `~/.vaudeville/logs/events.jsonl` — every classification (ts, rule, verdict, confidence, latency_ms, tier, reason, input_snippet)
- `~/.vaudeville/logs/violations.jsonl` — subset where `verdict == "violation"`
- `~/.vaudeville/logs/config.yaml` — retention settings (auto-created with defaults on first run)

### Retention

Loguru rotates each log file when it reaches `max_size_mb` and deletes rotated siblings older than `retention_days`. Defaults:

```yaml
max_size_mb: 10
retention_days: 7
```

Edit `~/.vaudeville/logs/config.yaml` to change these. Raise `max_size_mb` if you want `stats` to reflect a longer window — `stats` reads only the live `events.jsonl`, not rotated siblings, so once rotation fires only post-rotation data is aggregated.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) >= 0.4.27 (Python package manager)
- ~4 GB disk for the model
- Apple Silicon (recommended — MLX backend, GPU-accelerated) or x86_64 (GGUF backend, CPU only, slower)

## Development

```bash
just install    # install dependencies
just check      # lint + format + typecheck
just test       # run tests
just eval       # evaluate rules against test cases
```

## Troubleshooting

**Daemon not starting?**
Check that the model is downloaded to the Hugging Face cache: `ls ~/.cache/huggingface/hub/ | grep -i phi-4`. If nothing matches, run `/vaudeville:setup` again.

**Rules not firing?**
Verify rules are in `~/.vaudeville/rules/` and have valid YAML. Check the daemon socket exists: `ls /tmp/vaudeville-*/vaudeville.sock`.

**False positives?**
Raise the rule's `threshold` value in its YAML file (e.g., `threshold: 0.7` → `threshold: 0.85`). Higher threshold = fewer but more confident matches.

**Performance concerns?**
Inference runs locally on your hardware. Apple Silicon uses the MLX backend (~200ms per classification). x86_64 uses the GGUF backend via llama-cpp.

## License

[MIT](LICENSE)
