# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Decide rules can declare an optional `unsure:` gate (`below`, `action`, `outcomes?`) that substitutes an action when a `typesafe:*` model's confidence falls below a threshold

### Changed
- `events.jsonl` `confidence` is now nullable (null when the model reports none), and gains `unsure`, `unsure_below`, and `confidence_missing` fields
- Dataset-based rule evaluation now builds and scores inline test cases through pydantic-evals
- Rules are now typed `decide`/`rewrite` YAML (`type`, `outcomes`, `on`, `reasons`, `target`) validated by pydantic, replacing the untyped `labels`/`message`/`threshold` format
- Classification runs through pydantic-ai against a configurable hosted model provider (set in `~/.vaudeville/config`), replacing local Phi-4-mini inference
- `/vaudeville:setup` now runs a single `uv sync` and writes `~/.vaudeville/config` instead of selecting an MLX/GGUF backend
- `--json` emits rule summaries, and `--calibrate --rule` emits confidence reports.

### Removed
- MLX backend, GGUF backend, and the `vaudeville setup` model-download step
- The old `labels`/`message`/`threshold` rule fields
- `--cross-validate` and `just eval-cv` are no longer available.

### Upgrade Note
Old-format rules (`labels`/`message`/`threshold`, no `type:`) fail the typed
schema and are skipped silently at load, with only a daemon-log warning. Run
`vaudeville validate` to find rules skipped this way, then rewrite them as
`type: decide` (or `type: rewrite`) per the README "Authoring Rules" section.

## [0.1.0] - 2026-04-11

### Added
- SLM-powered semantic hook enforcement for Claude Code
- MLX backend (Apple Silicon) and GGUF backend (x86_64/Linux)
- YAML rule format with few-shot prompt templates
- Fail-open design — daemon down or inference error never blocks sessions
- Eval harness with leave-one-out cross-validation
- 25 Claude Code hook events supported
- Back-truncation and prompt injection defenses
- Draft sycophancy-detector rule with 72 eval cases from session JSONL
- Draft fake-green-detector rule with 47 eval cases from session JSONL
- Draft-skip support in `load_rules` so draft rules are excluded from loading/eval
- `--eval-log` flag for JSONL regression tracking in eval harness
- `repeat_penalty=1.1` to GGUF backend for reduced repetition
- Event timing guidance (PreToolUse vs PostToolUse vs Stop) in README
- Quick start guide, bundled rules table, and troubleshooting section in README

### Fixed
- Negation-aware verdict parsing — "not a violation" no longer matches as violation
- SentencePiece `▁` prefix normalization in `compute_confidence`

### Changed
- Split `eval.py` into `eval.py` + `eval_report.py` to stay within complexity budget
