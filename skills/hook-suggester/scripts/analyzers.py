#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys

DB_PATH = os.path.expanduser("~/.claude/analytics/sessions.duckdb")
DEFAULT_DAYS = 14
DEFAULT_MIN_OCCURRENCES = 3


def query(sql):
    """Run a DuckDB query and return parsed JSON results."""
    try:
        result = subprocess.run(
            ["duckdb", DB_PATH, "-json", "-c", sql],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        print("ERROR: duckdb not found on PATH", file=sys.stderr)
        sys.exit(1)
    if result.returncode != 0:
        print(
            f"WARNING: duckdb query failed (exit {result.returncode})",
            file=sys.stderr,
        )
        if result.stderr:
            print(f"  {result.stderr.strip()[:200]}", file=sys.stderr)
        return []
    raw = result.stdout.strip()
    if not raw or raw == "[{]":
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"WARNING: Failed to parse DuckDB JSON output: {e}", file=sys.stderr)
        return []


def check_dangerous_bash(days, min_occ):
    """Detect dangerous bash commands that should be guarded."""
    rows = query(f"""
        SELECT
            bash_cmd,
            count(*) AS uses
        FROM tool_uses
        WHERE tool_name = 'Bash'
          AND bash_cmd IS NOT NULL
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND (
            bash_cmd LIKE '%rm -rf%'
            OR (bash_cmd LIKE '%git push%--force%'
                AND bash_cmd NOT LIKE '%--force-with-lease%')
            OR bash_cmd LIKE '%DROP %'
            OR bash_cmd LIKE '%> /dev/%'
            OR bash_cmd LIKE '%chmod 777%'
            OR bash_cmd LIKE '%--no-verify%'
          )
        GROUP BY bash_cmd
        HAVING count(*) >= {min_occ}
        ORDER BY uses DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    examples = [r["bash_cmd"][:80] for r in rows]
    total = sum(int(r["uses"]) for r in rows)
    return {
        "id": "dangerous-bash",
        "event": "PreToolUse",
        "priority": "high",
        "title": "Guard dangerous bash commands",
        "description": (
            f"Found {total} uses of dangerous bash patterns across "
            f"{len(rows)} commands in the last {days} days."
        ),
        "examples": examples,
        "hook_type": "safety",
        "suggested_action": "block",
    }


def check_tool_misuse(days, min_occ):
    """Detect bash used for tasks that have dedicated tools."""
    rows = query(f"""
        SELECT
            CASE
                WHEN bash_cmd LIKE 'cat %' OR bash_cmd LIKE 'head %'
                    OR bash_cmd LIKE 'tail %' THEN 'cat/head/tail → Read tool'
                WHEN bash_cmd LIKE 'grep %' OR bash_cmd LIKE 'rg %'
                    OR bash_cmd LIKE 'egrep %' THEN 'grep/rg → Grep tool'
                WHEN bash_cmd LIKE 'find %' OR bash_cmd LIKE 'fd %'
                    THEN 'find/fd → Glob tool'
                WHEN bash_cmd LIKE 'sed %' OR bash_cmd LIKE '%sed -i%'
                    THEN 'sed → Edit tool'
                WHEN bash_cmd LIKE 'echo %>>%' OR bash_cmd LIKE 'echo %>%'
                    OR (bash_cmd LIKE 'cat %' AND bash_cmd LIKE '%>%')
                    THEN 'echo/cat redirect → Write tool'
            END AS misuse_type,
            count(*) AS uses
        FROM tool_uses
        WHERE tool_name = 'Bash'
          AND bash_cmd IS NOT NULL
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY misuse_type
        HAVING misuse_type IS NOT NULL AND count(*) >= {min_occ}
        ORDER BY uses DESC;
    """)
    if not rows:
        return None
    total = sum(int(r["uses"]) for r in rows)
    misuses = [f"{r['misuse_type']} ({r['uses']}x)" for r in rows]
    return {
        "id": "tool-misuse",
        "event": "PreToolUse",
        "priority": "medium",
        "title": "Redirect bash to dedicated tools",
        "description": (
            f"Found {total} bash calls that should use dedicated tools. "
            f"A PreToolUse hook can warn Claude to use the right tool."
        ),
        "examples": misuses,
        "hook_type": "quality",
        "suggested_action": "warn",
    }


def check_high_error_tools(days, min_occ):
    """Detect tools with high error rates."""
    rows = query(f"""
        SELECT
            tu.tool_name,
            count(*) AS total,
            sum(CASE WHEN tr.is_error = 'true' THEN 1 ELSE 0 END) AS errors,
            round(
                sum(CASE WHEN tr.is_error = 'true' THEN 1 ELSE 0 END)
                * 100.0 / count(*), 1
            ) AS error_pct
        FROM tool_uses tu
        JOIN tool_results tr ON tu.tool_use_id = tr.tool_use_id
        WHERE tu.timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY tu.tool_name
        HAVING count(*) >= {min_occ} AND error_pct > 20
        ORDER BY error_pct DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    tools = [f"{r['tool_name']} ({r['error_pct']}% errors, {r['total']} calls)" for r in rows]
    return {
        "id": "high-error-tools",
        "event": "PostToolUse",
        "priority": "medium",
        "title": "Add validation for error-prone tools",
        "description": (
            f"Found {len(rows)} tools with >20% error rate. "
            f"PostToolUse hooks can catch common failure patterns."
        ),
        "examples": tools,
        "hook_type": "quality",
        "suggested_action": "warn",
    }


def check_permission_friction(days, min_occ):
    """Detect frequent permission denials."""
    rows = query(f"""
        SELECT
            substr(content, 1, 100) AS denial,
            count(*) AS denials
        FROM permission_denials
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY denial
        HAVING count(*) >= {min_occ}
        ORDER BY denials DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    total = sum(int(r["denials"]) for r in rows)
    return {
        "id": "permission-friction",
        "event": "PreToolUse",
        "priority": "low",
        "title": "Reduce permission friction",
        "description": (
            f"Found {total} permission denials across {len(rows)} patterns. "
            f"Consider adding allowlist entries or a PreToolUse hook that "
            f"catches these before they hit the permission prompt."
        ),
        "examples": [r["denial"][:80] for r in rows],
        "hook_type": "workflow",
        "suggested_action": "info",
    }


def check_missing_quality_hooks(days, min_occ=None):  # noqa: ARG001
    """Detect if Stop hooks are underused."""
    hook_rows = query(f"""
        SELECT count(*) AS cnt
        FROM stop_hooks
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY;
    """)
    stop_rows = query(f"""
        SELECT count(*) AS cnt
        FROM stop_events
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY;
    """)
    hook_count = int(hook_rows[0]["cnt"]) if hook_rows else 0
    stop_count = int(stop_rows[0]["cnt"]) if stop_rows else 0

    if stop_count == 0:
        return None

    ratio = hook_count / stop_count if stop_count > 0 else 0
    if ratio > 0.5:
        return None

    return {
        "id": "missing-quality-hooks",
        "event": "Stop",
        "priority": "high",
        "title": "Add Stop hooks for quality enforcement",
        "description": (
            f"Only {hook_count}/{stop_count} stops "
            f"({ratio * 100:.0f}%) triggered quality hooks. "
            f"Stop hooks catch hedging, premature completion, and "
            f"unverified claims before they reach you."
        ),
        "examples": [],
        "hook_type": "quality",
        "suggested_action": "block",
    }


def check_hook_failures(days, min_occ):
    """Detect hooks that frequently error."""
    error_rows = query(f"""
        SELECT
            json_extract_string(err_element, '$') AS err,
            count(*) AS cnt
        FROM stop_hooks,
             unnest(json_extract(hookErrors, '$[*]')) AS t(err_element)
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND hookErrors IS NOT NULL
          AND json_array_length(hookErrors) > 0
        GROUP BY err
        HAVING count(*) >= {min_occ}
        ORDER BY cnt DESC
        LIMIT 5;
    """)
    if not error_rows:
        return None
    return {
        "id": "hook-failures",
        "event": "Stop",
        "priority": "high",
        "title": "Fix failing hooks",
        "description": (
            f"Found {len(error_rows)} hook error patterns. "
            f"Broken hooks silently pass, defeating enforcement."
        ),
        "examples": [r["err"][:100] for r in error_rows],
        "hook_type": "maintenance",
        "suggested_action": "fix",
    }


def check_code_write_volume(days, min_occ):
    """Detect languages with high code write volume."""
    rows = query(f"""
        SELECT
            CASE
                WHEN file_path LIKE '%.py' THEN 'Python'
                WHEN file_path LIKE '%.ts' OR file_path LIKE '%.tsx' THEN 'TypeScript'
                WHEN file_path LIKE '%.js' OR file_path LIKE '%.jsx' THEN 'JavaScript'
                WHEN file_path LIKE '%.rs' THEN 'Rust'
                WHEN file_path LIKE '%.go' THEN 'Go'
            END AS lang,
            count(*) AS writes
        FROM tool_uses
        WHERE tool_name IN ('Edit', 'Write')
          AND file_path IS NOT NULL
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY lang
        HAVING lang IS NOT NULL AND count(*) >= {min_occ}
        ORDER BY writes DESC;
    """)
    if not rows:
        return None
    langs = [f"{r['lang']} ({r['writes']} writes)" for r in rows]
    total = sum(int(r["writes"]) for r in rows)
    return {
        "id": "auto-format",
        "event": "PostToolUse",
        "priority": "low",
        "title": "Auto-format on file writes",
        "description": (
            f"Found {total} code file writes across {len(rows)} languages. "
            f"A PostToolUse hook can auto-run formatters after Edit/Write."
        ),
        "examples": langs,
        "hook_type": "workflow",
        "suggested_action": "info",
    }


def check_repeated_bash_patterns(days, min_occ):
    """Find frequently repeated bash commands that could be hooks."""
    rows = query(f"""
        SELECT
            bash_cmd AS cmd,
            count(*) AS uses
        FROM tool_uses
        WHERE tool_name = 'Bash'
          AND bash_cmd IS NOT NULL
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND length(bash_cmd) > 20
        GROUP BY bash_cmd
        HAVING count(*) >= {min_occ * 3}
        ORDER BY uses DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    cmds = [f"{r['cmd'][:80]} ({r['uses']}x)" for r in rows]
    return {
        "id": "repeated-commands",
        "event": "SessionStart",
        "priority": "low",
        "title": "Automate repeated commands",
        "description": (
            f"Found {len(rows)} bash commands repeated {min_occ * 3}+ times. "
            f"Consider automating via SessionStart or UserPromptSubmit hooks."
        ),
        "examples": cmds,
        "hook_type": "workflow",
        "suggested_action": "info",
    }


def check_correction_patterns(days, min_occ):
    """Mine user messages that correct Claude's behavior — SLM rule candidates."""
    rows = query(f"""
        SELECT
            substr(message::VARCHAR, 1, 150) AS user_msg,
            count(*) AS occurrences
        FROM raw_entries
        WHERE type = 'user'
          AND userType = 'external'
          AND length(message::VARCHAR) < 200
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND typeof(message) = 'VARCHAR'
          AND (message::VARCHAR ILIKE '%no,%'
               OR message::VARCHAR ILIKE '%wrong%'
               OR message::VARCHAR ILIKE '%that''s not%'
               OR message::VARCHAR ILIKE '%stop %doing%'
               OR message::VARCHAR ILIKE '%I said%'
               OR message::VARCHAR ILIKE '%not what I%')
        GROUP BY user_msg
        HAVING count(*) >= {min_occ}
        ORDER BY occurrences DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    total = sum(int(r["occurrences"]) for r in rows)
    examples = [r["user_msg"][:100] for r in rows]
    return {
        "id": "correction-patterns",
        "event": "Stop",
        "priority": "medium",
        "title": "User correction patterns — SLM rule candidates",
        "description": (
            f"Found {total} user corrections across {len(rows)} patterns "
            f"in the last {days} days. Each repeated correction is a "
            f"candidate SLM rule."
        ),
        "examples": examples,
        "hook_type": "slm-rule",
        "suggested_action": "warn",
    }


def check_retry_loops(days, min_occ):
    """Find tools called repeatedly on the same file with errors between."""
    rows = query(f"""
        WITH sequenced AS (
            SELECT
                tu.sessionId,
                tu.tool_name,
                tu.file_path,
                tu.timestamp,
                tr.is_error,
                LAG(tr.is_error) OVER (
                    PARTITION BY tu.sessionId, tu.file_path
                    ORDER BY tu.timestamp
                ) AS prev_error,
                LAG(tu.tool_name) OVER (
                    PARTITION BY tu.sessionId, tu.file_path
                    ORDER BY tu.timestamp
                ) AS prev_tool
            FROM tool_uses tu
            JOIN tool_results tr ON tu.tool_use_id = tr.tool_use_id
            WHERE tu.file_path IS NOT NULL
              AND tu.timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        )
        SELECT
            tool_name,
            count(*) AS retry_count
        FROM sequenced
        WHERE prev_error = 'true'
          AND tool_name = prev_tool
        GROUP BY tool_name
        HAVING count(*) >= {min_occ}
        ORDER BY retry_count DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    total = sum(int(r["retry_count"]) for r in rows)
    examples = [f"{r['tool_name']} ({r['retry_count']} retries)" for r in rows]
    return {
        "id": "retry-loops",
        "event": "PostToolUse",
        "priority": "medium",
        "title": "Error-retry loops — guessing instead of reading errors",
        "description": (
            f"Found {total} error→retry chains across {len(rows)} tools "
            f"in the last {days} days. A PostToolUse rule can catch "
            f"repeated failures on the same file."
        ),
        "examples": examples,
        "hook_type": "slm-rule",
        "suggested_action": "warn",
    }


def check_permission_tool_waste(days, min_occ):
    """Find tools that hit permission errors, burning turns a PreToolUse hook could intercept."""
    rows = query(f"""
        WITH perm_failures AS (
            SELECT
                tu.tool_name,
                tu.sessionId,
                tu.timestamp,
                tu.tool_use_id
            FROM tool_uses tu
            JOIN tool_results tr ON tu.tool_use_id = tr.tool_use_id
            WHERE tr.is_error = 'true'
              AND tu.timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
              AND (tr.content ILIKE '%permission denied%'
                   OR tr.content ILIKE '%permission%' AND tr.content ILIKE '%denied%'
                   OR tr.content ILIKE '%not allowed%' AND tr.content ILIKE '%permission%')
        )
        SELECT
            tool_name,
            count(*) AS failures
        FROM perm_failures
        GROUP BY tool_name
        HAVING count(*) >= {min_occ}
        ORDER BY failures DESC
        LIMIT 10;
    """)
    if not rows:
        return None
    total = sum(int(r["failures"]) for r in rows)
    examples = [f"{r['tool_name']} ({r['failures']} permission failures)" for r in rows]
    return {
        "id": "permission-tool-waste",
        "event": "PreToolUse",
        "priority": "low",
        "title": "Tools wasting turns on permission walls",
        "description": (
            f"Found {total} permission-related failures across "
            f"{len(rows)} tools in the last {days} days. "
            f"A PreToolUse hook can intercept before the turn is burned."
        ),
        "examples": examples,
        "hook_type": "workflow",
        "suggested_action": "warn",
    }


# --- Semantic candidates mined from final assistant messages -----------------
#
# Each category uses a lexical prefilter only. A model judges the candidates
# later, through the eval harness. Every category here targets a Stop rule that
# the next turn can correct at tier "block". Past-tense behavior that no later
# turn can fix (wasted time, tone) is out of scope on purpose.

MAX_EXAMPLES = 5
SNIPPET_CHARS = 240
TAIL_CHARS = 400

EDIT_TOOLS = "'Edit', 'Write', 'MultiEdit', 'NotebookEdit'"

# Bash commands that count as verification evidence for a completion claim.
VERIFY_CMD_PATTERN = (
    r"\b(pytest|unittest|jest|vitest|mocha|rspec|tsc|mypy|ruff|eslint|"
    r"cargo (test|build|check|clippy)|go (test|build|vet)|"
    r"(npm|pnpm|yarn|bun) (run )?(test|build|lint|check)|"
    r"dotnet (test|build)|make|just|gradle|mvn|rake)\b"
)

_SECRET_PATTERNS = [
    re.compile(r"\b(?:sk|ghp|gho|ghs|xox[abp]|AKIA|AIza)[-_A-Za-z0-9]{12,}"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b(\s*[:=]\s*)\S+"),
    re.compile(r"\b[A-Za-z0-9+/_-]{32,}\b"),
]


def _redact(text):
    """Remove likely secrets from an example snippet."""
    text = _SECRET_PATTERNS[0].sub("[REDACTED]", text)
    text = _SECRET_PATTERNS[1].sub("Bearer [REDACTED]", text)
    text = _SECRET_PATTERNS[2].sub(r"\1\2[REDACTED]", text)
    return _SECRET_PATTERNS[3].sub("[REDACTED]", text)


def _snippet(text, match, tail_only):
    """Cut a short, redacted excerpt that shows the matched behavior."""
    text = text.strip()
    if tail_only:
        start = max(0, len(text) - SNIPPET_CHARS)
    else:
        start = max(0, match.start() - SNIPPET_CHARS // 2)
    words = text[start : start + SNIPPET_CHARS].split()
    if start > 0 and len(words) > 1:
        words = words[1:]
    return _redact(" ".join(words))


_SEMANTIC_CATEGORIES = [
    {
        "id": "permission-to-git",
        "title": "Asks permission to commit, push, or open a PR",
        "priority": "high",
        "covered_by": "git-gate",
        "tail_only": True,
        "unverified_only": False,
        "pattern": re.compile(
            r"(?i)\b(should i|shall i|want me to|would you like me to|"
            r"do you want me to|ready to|let me know (if|when)|"
            r"say the word)\b[^.?!]{0,60}\b(commit|push|"
            r"(open|create|make|raise)\s+(a|the)\s+(pr|pull request)|merge)\b"
        ),
        "why": (
            "The work is done and the final message asks before the git step. "
            "A block at Stop makes the next turn do the git step."
        ),
    },
    {
        "id": "deferred-work",
        "title": "Defers work to a follow-up, later PR, or ticket",
        "priority": "high",
        "covered_by": "deferral-detector",
        "tail_only": False,
        "unverified_only": False,
        "pattern": re.compile(
            r"(?i)\b(follow[- ]?up (pr|ticket|issue|commit)|"
            r"in a (later|future|separate|follow[- ]?up) (pr|commit|change|"
            r"session|pass)|(left|leave|leaving) (it |this |that )?"
            r"(for|as) (a )?(later|follow[- ]?up)|"
            r"(can|could|should) be (done|addressed|handled|tackled) "
            r"(later|separately|afterwards)|"
            r"(file|create|open) (a |an )?(ticket|issue|follow[- ]?up)|"
            r"out of scope for (this|now))\b"
        ),
        "why": (
            "The final message pushes known work to later. A block at Stop "
            "makes the next turn do the work now."
        ),
    },
    {
        "id": "unverified-completion",
        "title": "Claims success after edits without running a test or build",
        "priority": "high",
        "covered_by": None,
        "tail_only": False,
        "unverified_only": True,
        "pattern": re.compile(
            r"(?i)\b(should (now |then )?(work|fix|be (fixed|working|good))|"
            r"(this|that|it) (now )?(fixes|fixed|works|resolves|resolved)|"
            r"(is|are) now (fixed|working|resolved|complete|done)|"
            r"all (set|done|good))\b"
        ),
        "why": (
            "The turn edited files, claimed success, and ran no test or build. "
            "A block at Stop makes the next turn run a check."
        ),
    },
    {
        "id": "hedged-claims",
        "title": "Hedges about facts the agent could check",
        "priority": "medium",
        "covered_by": None,
        "tail_only": False,
        "unverified_only": False,
        "pattern": re.compile(
            r"(?i)\b(i believe|probably|presumably|i assume|i think|likely)\b"
            r"[^.?!]{0,80}\b(exists?|passes|fails|installed|defined|imported|"
            r"returns?|supports?|version|deprecated|available)\b"
        ),
        "why": (
            "The agent guesses about a fact that a tool call can check. "
            "A block at Stop makes the next turn check it."
        ),
    },
]


def _final_message_rows(days):
    """Return final messages with edit and verification flags per turn."""
    return query(f"""
        SELECT
            fm.timestamp,
            fm.sessionId,
            left(fm.text, 3000) AS text,
            EXISTS (
                SELECT 1 FROM tool_uses tu
                WHERE tu.sessionId = fm.sessionId
                  AND tu.timestamp > fm.turn_start
                  AND tu.timestamp <= fm.timestamp
                  AND tu.tool_name IN ({EDIT_TOOLS})
            ) AS edited,
            EXISTS (
                SELECT 1 FROM tool_uses tu
                WHERE tu.sessionId = fm.sessionId
                  AND tu.timestamp > fm.turn_start
                  AND tu.timestamp <= fm.timestamp
                  AND tu.tool_name = 'Bash'
                  AND regexp_matches(tu.bash_cmd, '{VERIFY_CMD_PATTERN}')
            ) AS verified
        FROM final_messages fm
        WHERE fm.timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        ORDER BY fm.timestamp DESC
        LIMIT 20000;
    """)


def _mine_category(category, rows):
    """Count prefilter hits and collect distinct example snippets."""
    count = 0
    examples = []
    seen = set()
    for row in rows:
        text = row.get("text") or ""
        if category["unverified_only"] and not (row["edited"] and not row["verified"]):
            continue
        scope = text.strip()[-TAIL_CHARS:] if category["tail_only"] else text
        match = category["pattern"].search(scope)
        if not match:
            continue
        count += 1
        snippet = _snippet(scope, match, category["tail_only"])
        key = snippet.lower()
        if key not in seen and len(examples) < MAX_EXAMPLES:
            seen.add(key)
            examples.append(snippet)
    return count, examples


def check_semantic_candidates(days, min_occ):
    """Mine final assistant messages for semantic Stop-rule candidates.

    Returns a list with one suggestion per category that has enough signal.
    """
    rows = _final_message_rows(days)
    if not rows:
        return []
    suggestions = []
    for category in _SEMANTIC_CATEGORIES:
        count, examples = _mine_category(category, rows)
        if count < min_occ:
            continue
        covered = category["covered_by"]
        coverage = (
            f"Bundled rule `{covered}` already covers this."
            if covered
            else "No bundled rule covers this."
        )
        suggestions.append(
            {
                "id": f"semantic-{category['id']}",
                "event": "Stop",
                "priority": category["priority"],
                "title": category["title"],
                "description": (
                    f"Found {count} final messages in the last {days} days "
                    f"with this pattern (lexical match, not model-judged). "
                    f"{category['why']} {coverage}"
                ),
                "examples": examples,
                "hook_type": "slm-rule",
                "category": "semantic",
                "suggested_action": "shadow",
                "tier": "shadow",
                "target_tier": "block",
                "count": count,
                "covered_by": covered,
                "test_cases": [{"text": e, "outcome": "violation"} for e in examples],
            }
        )
    return suggestions
