<!-- Standalone spec. Owner-approved 2026-09-21. Not managed by the mold/cook flow. -->
<!-- Decision record: .cheese/decisions/jev-migration.md -->

# Typed rule core: decide and rewrite rules on pydantic-ai

## Problem

Rebuild the vaudeville rule and hook contract on pydantic and pydantic-ai, so that Jev classification, LLM judgement, and smart rewrite run as separate hook types across Claude Code, Codex, and other major harnesses.

Today one `Rule` dataclass (`vaudeville/core/rules.py:83`) drives a free-text `VERDICT:`/`REASON:` contract against a local Phi-4-mini model. The runner loads rules, builds prompts, and applies one `tier` action (`hooks/runner.py:109-112,171-189`). Jev returns typed values and no text, so the contract cannot carry it. This spec is the first of four; it delivers the core.

## Goals

- G-1: Rules are pydantic models with typed outputs that Jev can answer.
- G-2: Separate hook types exist for classify, judge, and rewrite.
- G-3: One rule set runs on Claude Code, Codex, and other major harnesses.
- G-4: pydantic-ai replaces the hand-rolled inference, and Phi-4-mini is removed.
- G-5: A `decide` rule maps its typed outcomes to actions on the hook or its output.

## Non-goals

- The tuning-loop redesign.
- TUI and `vaudeville watch` changes beyond new log fields.
- Adoption of a cross-harness hook specification.
- A `claude -p` subprocess model backend.

## Deferred follow-ups

- **FU-1b** — Eval on `pydantic-evals`, calibration, and the `commands/` and `skills/` prompts for the tune loop (F-11)
  - Destination: local_draft
  - State: prepared
  - Reference: .cheese/decisions/jev-migration.md
- **FU-2** — Codex CLI and Gemini CLI adapters (F-3, F-8, G-3)
  - Destination: local_draft
  - State: prepared
  - Reference: .cheese/decisions/jev-migration.md
- **FU-3** — TypeScript shims for OpenCode, Amp, Pi, and oh-my-pi that spawn the Python runner (F-3, F-8)
  - Destination: local_draft
  - State: prepared
  - Reference: .cheese/decisions/jev-migration.md
- **FU-4** — Cursor and Copilot CLI adapters
  - Destination: local_draft
  - State: prepared
  - Reference: .cheese/decisions/jev-migration.md
- **FU-5** — Python idioms and vulture jobs from `~/Dev/milknado`
  - Destination: local_draft
  - State: prepared
  - Reference: .cheese/decisions/jev-migration.md

## Grounding

| Probe | Outcome | Evidence |
| --- | --- | --- |
| wiki | unavailable | `ground` returned "hallouminate: not configured for this repo" |
| explorer | hit | Shape-check digest recorded in `.cheese/decisions/jev-migration.md`: verdict high; the runner loads rules today. `.cheese/research/` holds six researcher reports on Jev, harness hooks, prior art, and pydantic-ai |

## Approach

- Two rule types in a pydantic discriminated union on `type`: `decide` and `rewrite`. A judge is a `decide` rule with an LLM model. (F-1)
- The daemon stays. The runner stays stdlib and sends raw hook JSON. Rule loading, prompt building, and action dispatch move into the daemon. (F-2)
- This spec ships the Claude Code adapter only. The adapter seam is public so later specs add harnesses. (F-3)
- A `rewrite` rule gives corrective feedback, or mutates tool input on a per-rule allowlist of fields. It never mutates a raw `Bash` command. (F-4)
- A `decide` rule has an `on:` map from outcome to action. `tier` stays as the rollout ceiling from PR #95. An action references a `rewrite` rule by name. (F-6)
- The action set is `allow`, `log`, `warn`, `block`, `feedback`, `rewrite`, `escalate`, `ask`, `add-context`, `run`. (F-6b)
- `run` uses named commands whose argv lives only in `~/.vaudeville/config`. (F-7)
- Four specs land in order: this core, then eval and tune, then command-hook adapters, then TypeScript shims. (F-8)
- Until Jev access exists, the default `decide` model is a small hosted LLM. Jev is a one-line `model:` change per rule. (F-9)
- `~/.vaudeville/config` names the default model and the environment variable that holds its key. No provider is hard-coded. (F-10)
- This spec keeps a minimal eval that loads the new rule format. The `pydantic-evals` migration is FU-1b. (F-11)
- Phi-4-mini, the MLX and GGUF backends, `setup.py`, and the model download are removed.

## Decisions

- Hook types are `decide` + `rewrite` — Jev cannot write text; judge and classify differ only in model and reason. (F-1)
- Keep the daemon — pydantic-ai import costs 224-328 ms per process, measured. (F-2)
- Outcome map under a tier ceiling — Jev `Choice` and `Score` outcomes are not binary, and PR #95 holds. (F-6)
- Named commands for `run` — rules load from `project/.vaudeville/rules/`, so rule-carried argv lets a cloned repository execute code. (F-7)
- Provider keys in user config — the project runs on a Claude subscription through OAuth; direct API use of that token is unverified. (F-10)
- _Minor decisions:_ rules live in a new `vaudeville/rules/` slice; `model` is a per-rule field; the validator rejects `reason: text` on a Jev model; Jev rules use `reasons:` buckets; `escalate` allows one hop; the most restrictive action wins across rules; a per-rule `matcher` runs before any model call; `event_log.py` JSONL stays; rules stay YAML; the daemon calls only providers named in user config; `feedback` and `rewrite` text that reaches the agent starts with a hook-origin label that names the rule; an action that the event does not support degrades to `warn` and the log records the downgrade.
- _Claude Code evidence:_ the hooks reference (https://code.claude.com/docs/en/hooks, read 2026-09-21) lists `permissionDecision` values `allow`, `deny`, and `ask` on `PreToolUse`, and lists `additionalContext` and `updatedInput` per event. `ask` and `add-context` are therefore native on some events only.

## Acceptance

- AC-1: WHEN the daemon loads a YAML rule file THE SYSTEM SHALL validate it into `DecideRule` or `RewriteRule` by `type`, and SHALL skip and log an invalid rule while other rules load.  (F-1, G-1, G-2)
- AC-2: WHEN a `decide` rule names a `typesafe:` model and declares a free-text reason THE SYSTEM SHALL reject the rule at load with an error that names the rule.  (F-1, F-9, G-1)
- AC-3: WHEN the runner receives hook JSON on stdin THE SYSTEM SHALL send `{op: "hook", harness, event, cwd, payload}` to the daemon, print the returned stdout, exit with the returned exit code, and import neither `yaml` nor `pydantic`.  (F-2)
- AC-4: WHEN the daemon is unreachable, times out, or returns an error THE SYSTEM SHALL print the harness allow output and exit 0.  (F-2)
- AC-5: WHEN a `decide` rule returns an outcome THE SYSTEM SHALL apply the action that `on:` maps to that outcome, and SHALL allow when the outcome has no entry.  (F-6, G-5)
- AC-6: WHEN a rule `tier` is `disabled`, `shadow`, or `log` THE SYSTEM SHALL emit no harness action and start no `run` command, and WHEN the tier is `warn` THE SYSTEM SHALL replace `block`, `ask`, `rewrite`, and `feedback` with `warn`, SHALL cap the result of `escalate` the same way, and SHALL leave `allow`, `log`, `add-context`, and `run` unchanged.  (F-6, G-5)
- AC-7: WHEN the Claude Code adapter renders an action on an event that supports it THE SYSTEM SHALL emit the matching hook output for each of the ten actions, and WHEN the event does not support the action THE SYSTEM SHALL emit `warn` and log the downgrade.  (F-6b, F-3, G-5)
- AC-8: WHEN a `rewrite` rule mutates tool input THE SYSTEM SHALL change only the paths in its `target:` list, SHALL reject at load a target that resolves to a `Bash` command, and SHALL log the before and after values.  (F-4, G-2)
- AC-9: WHEN a `rewrite` action fires on an event that has no tool input THE SYSTEM SHALL deliver the text as `feedback` and log the downgrade.  (F-4, G-2)
- AC-10: WHEN an `escalate` action fires THE SYSTEM SHALL run the named `decide` rule once, and SHALL keep the first decision if the hook timeout expires.  (F-6b)
- AC-11: WHEN a `run` action fires THE SYSTEM SHALL start the named command from `~/.vaudeville/config` without a shell, with event JSON on stdin, a timeout, and no wait, and SHALL skip and log a name that the config does not define.  (F-7)
- AC-12: WHEN a project rule file defines command argv THE SYSTEM SHALL reject the rule at load.  (F-7)
- AC-13: WHEN a rule names no model THE SYSTEM SHALL use the default model from `~/.vaudeville/config`, and WHEN the key variable is unset THE SYSTEM SHALL allow the hook and write one notice to stderr.  (F-9, F-10, G-4)
- AC-14: WHEN a rule names a provider that `~/.vaudeville/config` does not list THE SYSTEM SHALL make no network call for that rule and SHALL allow.  (F-10)
- AC-15: WHEN the runner passes an unknown `--harness` value THE SYSTEM SHALL allow the hook.  (F-3, F-8)
- AC-16: WHEN several rules match one event THE SYSTEM SHALL apply the most restrictive of `block`, `ask`, `rewrite`, `feedback`, `warn`, and SHALL apply every `add-context`, `log`, and `run`.  (F-6b)
- AC-17: WHEN a rule `matcher` does not match the tool name THE SYSTEM SHALL make no model call for that rule.  (F-9)
- AC-18: WHEN hook text contains instructions or the data delimiter THE SYSTEM SHALL pass it to the model as delimited data, and SHALL discard a `rewrite` output that exceeds the length cap.  (F-4)
- AC-19: WHEN `just build` runs THE SYSTEM SHALL pass with no `mlx`, `gguf`, `huggingface-hub`, or Phi reference in the package, the justfile, the CI workflow, or `hooks/session-start.sh`.  (G-4)
- AC-20: WHEN `just eval` runs THE SYSTEM SHALL load the bundled rules in the new format and report tp, fp, tn, and fn per rule.  (F-11, F-8)
- AC-21: WHEN a decision completes THE SYSTEM SHALL append the rule, outcome, action, model, confidence, and latency to `events.jsonl`.  (F-6)
- AC-22: WHEN `feedback` or `rewrite` text reaches the agent THE SYSTEM SHALL start the text with a hook-origin label that names the rule.  (F-4)
- AC-23: WHEN a `decide` rule runs THE SYSTEM SHALL get the outcome from a pydantic-ai agent whose output type is built from the rule `outcomes`, and WHEN the rule names a `typesafe:` model THE SYSTEM SHALL build a `TypeSafeModel` for it.  (F-9, G-4, G-1)
- AC-24: WHEN a rule file changes on disk, or a request carries another project `cwd`, THE SYSTEM SHALL decide with the rules of that project root as they are on disk at request time.  (F-2)
- AC-25: WHEN `just build` runs THE SYSTEM SHALL pass with no `vaudeville/core/rules.py`, no `vaudeville/core/examples.py`, and no `parse_verdict`, `compute_confidence`, or `sanitize_input` symbol in the package, and `vaudeville.core` SHALL export no `Rule`.  (F-1, F-2, G-4)
- AC-26: WHEN a `decide` rule declares `reasons:` and the action is `warn` or `block` THE SYSTEM SHALL put the description of the selected reason in the message.  (F-6, G-1)

## Test Contracts

| Acceptance ID | Interface referent | Outermost stable seam | Expected failure | Mode | Interface version | Matrix rows |
| --- | --- | --- | --- | --- | --- | --- |
| AC-1 | `rules.load_layered` (F-1) | daemon `handle_request` | a `type: rewrite` YAML raises, because `Rule` has no `type` | contract-matrix | rules-v2 | decide-valid<br>rewrite-valid<br>unknown-type<br>unknown-field |
| AC-2 | `rules.DecideRule` (F-1, F-9) | daemon `handle_request` | a Jev rule with free-text reason loads without error | guard | | |
| AC-3 | `hooks/runner.py` main (F-2) | runner stdin and stdout | `yaml` is in `sys.modules` after the runner imports | tracer | | |
| AC-4 | `hooks/runner.py` main (F-2) | runner stdin and stdout | no `{op: "hook"}` request exists, so the fake socket sees `prompt` | guard | | |
| AC-5 | `on:` outcome map (F-6) | daemon `handle_request` | outcome `ticket-instead` blocks, because only `tier` decides | tracer | | |
| AC-6 | tier ceiling (F-6) | daemon `handle_request` | a `warn` tier emits `decision: block` | contract-matrix | rules-v2 | disabled<br>shadow<br>log-no-run<br>warn-caps-block<br>warn-caps-ask<br>warn-caps-rewrite<br>warn-caps-feedback<br>warn-caps-escalate-result<br>warn-keeps-add-context<br>warn-keeps-run<br>block |
| AC-7 | `harness.Adapter.render` (F-6b, F-3) | daemon `handle_request` | `ask` has no renderer | contract-matrix | claude-code-hooks | allow<br>log<br>warn<br>block<br>feedback<br>rewrite<br>escalate<br>ask<br>add-context<br>run<br>ask-on-Stop-degrades<br>add-context-on-Stop-degrades |
| AC-8 | `rules.RewriteRule.target` (F-4) | daemon `handle_request` | a `tool_input.command` target loads | guard | | |
| AC-9 | rewrite downgrade (F-4) | daemon `handle_request` | a Stop event returns `updatedInput` | guard | | |
| AC-10 | `escalate` action (F-6b) | daemon `handle_request` | the second rule never runs | tracer | | |
| AC-11 | named command runner (F-7) | daemon `handle_request` | no process starts for `run: notify` | tracer | | |
| AC-12 | rule loader (F-7) | daemon `handle_request` | a project rule with argv loads | guard | | |
| AC-13 | user config loader (F-9, F-10) | runner stdin and stdout | an unset key raises instead of allowing | guard | | |
| AC-14 | provider allowlist (F-10) | daemon `handle_request` | the `FunctionModel` records a call for an unlisted provider | guard | | |
| AC-15 | `harness.get_adapter` (F-3, F-8) | runner stdin and stdout | `--harness nope` exits non-zero | guard | | |
| AC-16 | action precedence (F-6b) | daemon `handle_request` | the first matching rule wins | contract-matrix | rules-v2 | block-over-warn<br>ask-over-rewrite<br>context-accumulates |
| AC-17 | rule `matcher` (F-9) | daemon `handle_request` | the `FunctionModel` call count is 1 for a non-matching tool | guard | | |
| AC-18 | data delimiting (F-4) | daemon `handle_request` | delimiter text appears unescaped in the model input | guard | | |
| AC-19 | package and build files | `just build` | `vaudeville/server/mlx_backend.py` exists | guard | | |
| AC-20 | `vaudeville eval` CLI (F-11, F-8) | `just eval` | the loader rejects `examples/rules/git-gate.yaml` in the new format | tracer | | |
| AC-21 | `server/event_log.py` (F-6) | `events.jsonl` | the record has no `outcome` or `action` field | tracer | | |
| AC-22 | hook-origin label (F-4) | daemon `handle_request` | the `feedback` text starts with the model output | guard | | |
| AC-23 | `server.agents` decide agent (F-9) | daemon `handle_request` | the `FunctionModel` records no call, because no pydantic-ai agent exists | tracer | | |
| AC-24 | `rules.load_layered` cache (F-2) | daemon `handle_request` | the second request returns the outcome of the rule text before the edit | contract-matrix | rules-v2 | file-edited<br>file-added<br>file-removed<br>second-project-root |
| AC-25 | `vaudeville.core` exports (F-1, F-2) | `just build` | `from vaudeville.core import Rule` succeeds | guard | | |
| AC-26 | `DecideRule.reasons` (F-6) | daemon `handle_request` | the block message holds the bucket id, not its description | tracer | | |

## Interface sketches

```text
slice:            NEW SLICE vaudeville/rules/; NEW vaudeville/server/agents/; NEW vaudeville/server/harness/
spine step:       entry hooks/runner.py -> infra core/client.py -> workflow server/_handlers.py -> domain rules/ -> infra server/agents/
public interface: rules.Rule = DecideRule | RewriteRule  (F-1)
                  rules.load_layered(project_root) -> RuleSet  (F-1, F-11)
                  DecideRule.on: dict[outcome, Action]; tier is the ceiling  (F-6)
                  rules.Action: allow|log|warn|block|feedback|rewrite|escalate|ask|add-context|run  (F-6b)
                  RewriteRule.target: list[dotted path]; no Bash command  (F-4)
                  wire: {op:"hook", harness, event, cwd, payload} -> {stdout, exit_code}  (F-2)
                  server.harness.Adapter.normalize(raw) -> HookEvent; .render(Outcome) -> dict  (F-3, F-8)
                  config: default_model, providers{name: key_env}, commands{name: argv}  (F-7, F-9, F-10)
private:          rule cache by project root and mtime (AC-24); escalation (AC-10); command runner (AC-11); data delimiting (AC-18)
crust delta:      core/ drops rules.py, examples.py, parse_verdict, compute_confidence; the socket contract changes
arrows:           server -> rules; server -> core; rules -> none; hooks/runner.py -> core only
```

## Risks

- Transcripts leave the machine; Phi ran locally. AC-14 limits providers to the user's list.
- A hosted LLM on every matching hook call adds latency and cost until Jev access exists. AC-17 limits calls.
- Model text flows back into an agent through `feedback` and `rewrite`. AC-8 and AC-18 bound it.
- Jev latency, accuracy, and determinism are vendor claims. No live call is tested.
- Tests: 46 files and 16,976 lines; many target removed code.
- The tune loop prompts teach the old schema until FU-1b lands.

## Open questions

- [TBD] The exact interim model id; verify at cook time.
- [TBD] The Claude Code output field for each action on each event; verify against the current hooks reference at cook time.
- [?] Whether `Dataset.evaluate_sync` works with `TypeSafeModel`; FU-1b owns it.

## Quality gates

- `just build`
- `just eval`

## Curds

8 curds in 6 waves. The owner approved this plan on 2026-09-21 after the plan was displayed. Land the waves as a linear stack; each layer passes `just build`.

| Wave | Curd | Outcome | Owns | Depends on |
| --- | --- | --- | --- | --- |
| 1 | `curd-01-rules-slice` | A new `vaudeville/rules` slice validates YAML rule files into `DecideRule \| RewriteRule`, serves `load_layered(project_root) -> RuleSet` from a cache keyed by project root and mtime, and carries the rule admin helpers; the project dependencies and build files are switched from the mlx/gguf groups to pydantic and pydantic-ai, with the old code untouched. | AC-1, AC-2, AC-12, AC-24 | none |
| 2 | `curd-02-agent-models` | `vaudeville/server/agents` builds a pydantic-ai decide agent whose output type comes from the rule `outcomes` (TypeSafeModel for `typesafe:` models), a rewrite agent with a length cap, and delimited-data prompts, all gated by a user config loader for `~/.vaudeville/config` (default_model, providers, commands). | AC-13, AC-14, AC-18, AC-23 | rules-slice |
| 2 | `curd-03-harness-claude-code` | `vaudeville/server/harness` exposes the public `Adapter` seam (`normalize(raw) -> HookEvent`, `render(Outcome) -> dict`), `get_adapter(name)`, and a Claude Code adapter that renders all ten actions per event and degrades unsupported ones to `warn` with a logged downgrade. | AC-7 | rules-slice |
| 3 | `curd-04-effect-primitives` | `vaudeville/server/effects` provides the standalone effects the pipeline will call: allowlisted tool-input mutation with before/after logging, rewrite-to-feedback downgrade when the event has no tool input, a one-hop escalation helper bounded by a deadline, a fire-and-forget named-command runner fed from user config, and the hook-origin label for agent-facing text. | AC-8, AC-9, AC-10, AC-11, AC-22 | rules-slice, agent-models, harness-claude-code |
| 4 | `curd-05-decide-pipeline` | `vaudeville.server.hook.handle_hook_request` answers `{op: "hook", harness, event, cwd, payload}` with `{stdout, exit_code}`: it loads rules for the request cwd, filters by event and `matcher`, runs decide agents, maps outcomes through `on:` under the tier ceiling, merges actions by precedence, invokes the effects, renders through the adapter, and logs each decision; the legacy daemon ops are untouched. | AC-5, AC-6, AC-16, AC-17, AC-21, AC-26 | rules-slice, agent-models, harness-claude-code, effect-primitives |
| 3 | `curd-06-eval-cli-migration` | The bundled example rules are in the new format, and `vaudeville eval`, `vaudeville/cli_rules.py`, and `vaudeville/__main__.py` load rules through `vaudeville.rules` and evaluate through the pydantic-ai decide agent; the eval-only `DaemonBackend`, the `setup` subcommand, and the Phi-era re-exports in `vaudeville/server/__init__.py` are gone. | AC-20 | rules-slice, agent-models |
| 5 | `curd-07-daemon-cutover` | The daemon serves only `op: hook` (routed to `handle_hook_request`) and starts with no local model; the classify and condense ops, the inference backend protocol, the MLX and GGUF backends, `vaudeville/setup.py`, the model download script, and the session-start model gate are deleted. | AC-19 | decide-pipeline, eval-cli-migration |
| 6 | `curd-08-runner-cutover` | `hooks/runner.py` is a stdlib-only pipe that sends `{op: "hook", harness, event, cwd, payload}` and prints the daemon's stdout with its exit code, failing open on every error and on an unknown `--harness`; the old rule loader, examples renderer, and text verdict contract are deleted from `vaudeville/core`. | AC-3, AC-4, AC-13, AC-15, AC-25 | daemon-cutover |

## Open items for the owner

- P1: `vaudeville/server/condense.py` (model-based condense) has no replacement here. `daemon-cutover` deletes it. Stdlib truncation stays.
- P2: Between `daemon-cutover` and `runner-cutover`, the old runner fails open. Land the two waves together.
- P3: `rules-slice` removes the `mlx` and `gguf` groups before the backend files go. Verify that tests mock those imports.
- P5: Verify coverage thresholds: `justfile` versus `CLAUDE.md`.
