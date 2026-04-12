# Vaudeville

SLM-powered semantic hook enforcement for [Claude Code](https://claude.ai/code). Classifies AI assistant output against YAML rules using local Phi-4-mini inference.

## How It Works

Vaudeville runs a local inference daemon (Phi-4-mini, 3.8B params, int4) that classifies Claude Code's output in real time. You write rules as YAML files with few-shot prompt templates. The daemon evaluates them on every hook event and returns block/warn/log verdicts.

**Fail-open by design** — if the daemon is down, the model isn't downloaded, or inference errors out, your session continues normally. Vaudeville never blocks you from working.

## Install

```
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

## Bundled Rules

These ship with vaudeville and are ready to use out of the box:

| Rule | Event | What it catches |
|---|---|---|
| `violation-detector` | Stop | Hedging, TODOs, unresolved findings in final responses |
| `dismissal-detector` | Stop | Dismissing test/CI failures without evidence |
| `hedging-detector` | Stop | Uncertain language about code that should have been verified |
| `deferral-detector` | PreToolUse | PR replies that defer reviewer concerns to "follow-up PRs" |

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

- **Reviewing what the assistant said** → `Stop`. Most quality rules (violation-detector, dismissal-detector, hedging-detector) use this because they evaluate the assistant's final output.
- **Blocking a bad action before it happens** → `PreToolUse`. The deferral-detector uses this to catch low-quality PR replies before they're posted.
- **Checking what a tool returned** → `PostToolUse`. Use this when the rule needs to see both the tool input and its result.

Other events (`SessionStart`, `UserPromptSubmit`, `Notification`, etc.) are available for specialized use cases. See `hooks/hooks.json` for the full list of wired events.

### Backend Differences

The GGUF backend (llama-cpp-python) enforces the `VERDICT: .../REASON: ...` output format via GBNF grammar-constrained decoding, guaranteeing structurally valid responses. The MLX backend relies on prompt compliance alone, as MLX-LM does not support GBNF grammars. Both backends use temperature 0.0 for deterministic inference; the GGUF backend additionally applies `repeat_penalty=1.1`.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- ~4 GB disk for the model
- Apple Silicon (MLX backend) or x86_64 (GGUF backend)

## Development

```bash
just install    # install dependencies
just check      # lint + format + typecheck
just test       # run tests
just eval       # evaluate rules against test cases
```

## Troubleshooting

**Daemon not starting?**
Check that the model is downloaded: `ls ~/.vaudeville/models/`. If empty, run `/vaudeville:setup` again.

**Rules not firing?**
Verify rules are in `~/.vaudeville/rules/` and have valid YAML. Check the daemon socket exists: `ls /tmp/vaudeville-*.sock`.

**False positives?**
Raise the rule's `threshold` value in its YAML file (e.g., `threshold: 0.7` → `threshold: 0.85`). Higher threshold = fewer but more confident matches.

**Performance concerns?**
Inference runs locally on your hardware. Apple Silicon uses the MLX backend (~200ms per classification). x86_64 uses the GGUF backend via llama-cpp.

## License

[MIT](LICENSE)
