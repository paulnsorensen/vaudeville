#!/usr/bin/env python3
"""Hook execution stats — coverage ratio and failure counts.

Usage: python3 hook_stats.py [--days 14] [--json]
"""

import json
import sys
from _db import parse_days, query


def main() -> None:
    args = sys.argv[1:]
    days = parse_days(args)

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
    error_rows = query(f"""
        SELECT
            json_extract_string(err_element, '$') AS error,
            count(*) AS cnt
        FROM stop_hooks,
             unnest(json_extract(hookErrors, '$[*]')) AS t(err_element)
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
          AND hookErrors IS NOT NULL
          AND json_array_length(hookErrors) > 0
        GROUP BY error
        ORDER BY cnt DESC
        LIMIT 10;
    """)

    hooks = int(hook_rows[0]["cnt"]) if hook_rows else 0
    stops = int(stop_rows[0]["cnt"]) if stop_rows else 0
    coverage = round(hooks / stops * 100, 1) if stops > 0 else 0

    result = {
        "days": days,
        "stop_events": stops,
        "hook_executions": hooks,
        "coverage_pct": coverage,
        "errors": error_rows,
    }

    if "--json" in args:
        print(json.dumps(result, indent=2))
    else:
        print(f"Stop events:     {stops}")
        print(f"Hook executions: {hooks}")
        print(f"Coverage:        {coverage}%")
        if error_rows:
            print(f"\nHook errors ({len(error_rows)} patterns):")
            for r in error_rows:
                print(f"  {r['cnt']}x  {r['error'][:80]}")


if __name__ == "__main__":
    main()
