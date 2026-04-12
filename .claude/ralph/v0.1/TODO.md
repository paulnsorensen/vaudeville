# Vaudeville v0.1 Ship Readiness — Ralph Work Queue

Executed by ralphify. One item per iteration. See `RALPH.md` for rules.
Spec: `.claude/specs/v0.1-ship-readiness.md` (frozen, do not edit).

Tag legend:
- `[ ]` — unchecked, do this next
- `[x]` — done (include commit hash)
- `[BLOCKED: reason]` — cannot proceed without unblock
- `[TBD-PAUL: question]` — needs human decision, skip
- `[SKIP: reason]` — intentionally skipped

---

## WS1 — Plumbing Fixes (do first, no design debate)

- [x] **WS1-01** (606dc02) Fix negation-aware verdict parsing in `core/protocol.py:59`.
      Current `re.compile(r"\bviolation\b")` matches inside "not a violation".
      Solution: pre-strip or lookbehind for negation tokens (`not`, `no`,
      `isn't`, `is not`). Add a test to `tests/test_core.py` covering:
      "not a violation" → clean, "no violation here" → clean, "this is a
      violation" → violation, "violation of trust" → violation.
      Gate: `just check && just test` both green.

- [x] **WS1-02** (def45ef) Add SentencePiece `▁` normalization in `compute_confidence`
      at `core/protocol.py:93`. Change the normalize step to
      `normalized = token.strip().lstrip("▁").lower()`. Add a test in
      `tests/test_core.py` for a logprobs dict containing `▁violation` and
      `▁clean` tokens — must return non-zero confidence.
      Gate: `just check && just test` both green.

- [x] **WS1-03** DECIDED 2026-04-11: keep `0.0` fail-open. Rationale: no
      reportable confidence means no blocking — aligns with fail-open
      invariant. Existing `logging.warning` in `compute_confidence` at
      `core/protocol.py:92,106` is sufficient instrumentation. No code
      change needed; this item is closed.

- [x] **WS1-04** Document the PreToolUse vs PostToolUse vs Stop event
      timing guidance in `README.md` (create the section if README is
      missing the rules authoring portion). Reference: `hooks/hooks.json`
      wires all three event types; each rule picks its `event:` field.
      Explain when to use which. No code change.
      Gate: `just check` green, no test change needed.

---

## WS2 — SLM Tuning (mechanical parts only; design parts are TBD-PAUL)

- [x] **WS2-01** Split `vaudeville/eval.py` (419 to 277 lines). Extracted
      `print_results`, `threshold_sweep`, `cross_validate_rule`, and
      `run_evaluations` into `vaudeville/eval_report.py` (163 lines).
      Updated all test imports. `just check && just test` green.

- [x] **WS2-02** Add `repeat_penalty=1.1` to the GGUF backend
      `create_chat_completion` call at `vaudeville/server/gguf_backend.py`
      (both `classify` line ~41 and `classify_with_logprobs` line ~57).
      No MLX change (top_p/top_k irrelevant at temp=0).
      Gate: `just check && just test && just eval` all green, no regression
      in per-rule precision/recall vs the eval JSONL from the previous
      commit on `main`.

- [x] **WS2-03** Add terse role-only system prompt to Phi-4-mini in
      `_apply_chat_template` on BOTH backends (`vaudeville/server/mlx_backend.py`
      and `vaudeville/server/gguf_backend.py`). Use EXACTLY this text as
      the system message content:

      ```
      You are a binary classifier. Respond with exactly `VERDICT: violation`
      or `VERDICT: clean` followed by `REASON: <one sentence>`. No other text.
      ```

      Prepend the system message to the `messages` list before
      `apply_chat_template`. GGUF backend currently has no
      `_apply_chat_template` helper — add one mirroring MLX's shape, or
      pass system via `create_chat_completion(messages=[...])` directly.
      Add a test in `tests/test_mlx_backend.py` (guarded by
      `importlib.util.find_spec`) and `tests/test_gguf_backend.py` asserting
      the system message is present in the formatted prompt.
      Gate: `just check && just test` green. Run `just eval` locally; no
      per-rule precision/recall regression vs last eval-history.jsonl line.

- [x] **WS2-04** Added GBNF grammar-constrained decoding to GGUF backend,
      default-on. Grammar cached at module scope. 3 new tests. Backend
      divergence documented in README. 406 tests pass.
      Original: Add GBNF grammar-constrained decoding to the GGUF backend,
      DEFAULT-ON. Grammar definition:

      ```
      root ::= "VERDICT: " verdict "\nREASON: " reason
      verdict ::= "violation" | "clean"
      reason ::= [^\n]{1,200}
      ```

      Pass via `create_chat_completion(grammar=LlamaGrammar.from_string(...))`
      in both `classify` and `classify_with_logprobs` in
      `vaudeville/server/gguf_backend.py`. Cache the compiled grammar at
      module scope (don't recompile per call). MLX backend: no change —
      MLX-LM does not support GBNF. Document the backend divergence in
      `README.md` under the existing event-timing section (one paragraph:
      "GGUF enforces output format via GBNF; MLX relies on prompt
      compliance"). Add a test asserting grammar is passed to
      `create_chat_completion` (mock the llama-cpp call).
      Gate: `just check && just test` green, `just eval` shows no
      regression.

- [x] **WS2-05** Added `--calibrate <rule>` to eval harness and
      `just eval-calibrate <rule>` recipe. Sweeps thresholds 0.30-0.90,
      picks F1-optimal at >=95% precision, writes back to rule YAML.
      Refuses if <20 cases. 10 new tests (416 total). No bundled rules
      calibrated -- Paul-reviewed step post-ship.
      Gate: `just check && just test` green.

- [x] **WS2-06** Add JSONL regression tracking to `just eval`. Added
      `--eval-log <path>` flag; each run appends one JSONL line with
      timestamp, model, git_head, per-rule precision/recall/f1.
      `tests/eval-history.jsonl` gitignored. 7 new tests (400 total).
      Gate: `just check && just test` green.

---

## WS3 — Default Rules + GTM Phase 0 (data + docs)

- [x] **WS3-01** Created `examples/rules/sycophancy-detector.yaml` with
      `draft: true` and placeholder prompt. Added draft-skip support to
      `load_rules` so draft rules are excluded from loading/eval. Test added.
      401 passed. Gate: `just check && just test` green.

- [x] **WS3-02** Created `examples/rules/fake-green-detector.yaml`. Same
      pattern as WS3-01 — draft placeholder, `examples/rules/` only, not
      wired.
      Gate: `just check` green.

- [x] **WS3-03** Seeded `tests/sycophancy-detector.yaml` with 3 VIOLATION
      and 2 CLEAN cases from spec. Added `get_draft_rule_names` helper to
      `core/rules.py` and updated `test_sufficient_cases_per_rule` to allow
      5-case minimum for draft rules. Valid YAML, 5 cases.
      Gate: `just check && just test` green.

- [x] **WS3-04** Seeded `tests/fake-green-detector.yaml` with 3 VIOLATION
      and 2 CLEAN cases from spec. Same pattern as WS3-03. 5 cases total.
      Gate: `just check && just test` green.

- [x] **WS3-05** Expanded `tests/sycophancy-detector.yaml` to 72 cases
      (37 violation / 35 clean = 51.4%/48.6%) by mining session JSONL.
      All verbatim quotes from real sessions. 14 opener types covered.
      Gate: `just check && just test` green, 72 cases, balance within tolerance.

- [x] **WS3-06** Expanded `tests/fake-green-detector.yaml` to 47 cases
      (22 violation / 25 clean = 46.8%/53.2%) by mining session JSONL.
      All verbatim quotes from real sessions. 6 violation categories:
      should/will predictions, stale diagnostic dismissals, deferred
      verification, unverified completion claims, pre-existing dismissals.
      Gate: `just check && just test` green, 47 cases, balance within tolerance.

- [x] **WS3-07** Updated `README.md` with v0.1 Phase 0 content: 5 minutes
      to first hook quick start, bundled rules table (4 shipped rules),
      custom rules section with link to `examples/rules/`, troubleshooting
      stub (daemon, rules, false positives, performance). 2-minute read.
      Gate: `just check` green. Manual review by Paul before shipping.

- [x] **WS3-08** Create `CHANGELOG.md` with a v0.1.0 entry grouping commits
      made in this worktree by type (fix/feat/refactor/test/docs). Pull
      from `git log --oneline` since the earliest WS commit. Keep-A-
      Changelog format.
      Gate: `just check` green.

- [SKIP: already present] **WS3-09** MIT LICENSE already exists and is
      populated. No action needed.
      Gate: `just check` green.

- [x] **WS3-10** Verified and updated `.claude-plugin/marketplace.json` schema is valid
      and v0.1-ready. If any required fields are missing or stale, update
      them. Do NOT bump the version field — that's Paul's call at release
      time.
      Gate: `just check` green, `jq . .claude-plugin/marketplace.json`
      exits 0.

---

## Done criteria (all must hold before v0.1 is shippable)

These are checked by Paul, not Ralph. Ralph stops when every unchecked
actionable item above is either `[x]` or `[BLOCKED]` or `[TBD-PAUL]`.

- All WS1 items green or TBD-PAUL
- All WS2 mechanical items green (TBD-PAUL items left for Paul)
- All WS3 items green (seed + expand + docs)
- `just check && just test && just coverage && just eval` all green
- No regressions in per-rule precision/recall vs baseline
- No pre-existing breaks introduced
