# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
