# Age Fixes TODO

Each iteration picks the first unchecked item, implements it with tests, marks it done, and commits.

## Spec Wiring (unwired ralph features)

- [x] US-001: Wire LLMSampler as default sampler in `run_study` — import from `sampler.py`, instantiate with optional Anthropic client, pass to `create_study`. TPE remains the fallback when no client is available.
- [x] US-002: Replace TPESampler default with NSGAIISampler — add `constraints_func` that reads `constraint_violated` user attr already set in `run_trial`. Keep TPE as the independent sampler fallback inside LLMSampler.
- [x] US-003: Wire `pool.py` authoring into `run_study` loop — re-add `author: bool` to `StudyConfig`, re-add `--author` CLI flag. When enabled, check `should_author()` each trial; on trigger call `author_candidates → inject_candidates`. Update tests.
- [x] US-004: Auto-start daemon in `_build_backend` — if `daemon_is_alive()` is false and `--no-daemon` not set, attempt `subprocess.Popen` to start the daemon, wait briefly, retry `daemon_is_alive()`. Fall back to MLXBackend if start fails.

## Complexity Extractions

- [x] US-005: Split `eval.py` (379→<300 lines) — extract `_build_parser`, `main`, and CLI entrypoint into `vaudeville/eval_cli.py`. Keep `classify_case`, `evaluate_rule`, `load_test_cases` in `eval.py`. Update `__main__.py` and `pyproject.toml` entry points. Fix all test imports.
- [x] US-006: Split `harness.py` (332→<300 lines) — move `StudyConfig`, `TuneVerdict` dataclasses and `create_study` into `vaudeville/tune/study.py`. `harness.py` becomes a thin coordinator. Fix all imports.
- [x] US-007: Shrink `run_study` (70→<40 lines) — extract `_extract_best_result(completed, best_ids)` (~16 lines) and `_run_study_loop(study, rule, ...)` (~22 lines). Introduce `TrialContext` dataclass to bundle `rule, tune_cases, held_cases, backend, config` — fixes the 6-param `run_trial` and 7-param `run_study` violations simultaneously.
- [x] US-008: Shrink `eval_cli.py:main` (50→<40 lines) — extract `_apply_extra_test_file(args, test_suites)` helper for the `if args.test_file and args.rule` block.
- [x] US-009: Fix `eval_report.py:calibrate_rule` 5 params — merge `rule_name` + `rules` since caller already has the Rule object; accept `(rule: Rule, cases, backend, rule_file)`.

## Nesting Fixes

- [ ] US-010: Extract `_find_best_completed(completed, best_ids)` from `harness.py:294` — depth 4 → depth 2.
- [ ] US-011: Extract `_bucket_for_latency(lat) -> str` from `stats.py:106` — depth 3 → depth 1.

## Encapsulation Fixes

- [ ] US-012: Fix `eval.py:19` — add `ClassifyResult` and `compute_confidence` to `core/__init__.py` exports. Change eval.py to import from `..core` instead of `..core.protocol`.
- [ ] US-013: Fix `tune/cli.py:15-16` — change to `from ..server import DaemonBackend, daemon_is_alive, InferenceBackend`. Verify symbols are in `server/__init__.py`.
- [ ] US-014: Fix `tune/harness.py:18` — change to `from ..server import InferenceBackend`.
- [ ] US-015: Fix `tune/cli.py:17` private import — rename `_format_verdict` to `format_verdict`, export from `tune/__init__.py`. Populate the barrel file with public API.

## Safety Fixes

- [ ] US-016: Fix `split_prompt` in `core/rules.py:136` — add guard: `if "{text}" not in prompt_with_context: return prompt_with_context, 0` before the `partition` call. Add test for rule without `{text}` placeholder.
- [ ] US-017: Fix `write_candidates` in `pool.py:143` — log warning and return early (don't overwrite) when existing YAML is non-dict. Add test for corrupt YAML scenario.
