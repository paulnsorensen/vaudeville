#!/usr/bin/env python3
"""Extract denied tool names from permission_denials, ranked by frequency.

Usage: python3 denied_tools.py [--days 14] [--limit 15] [--json]
"""

import sys
from _db import output, parse_days, parse_limit, query


def main() -> None:
    args = sys.argv[1:]
    days = parse_days(args)
    limit = parse_limit(args)

    rows = query(f"""
        SELECT
            CASE
                WHEN content LIKE 'Permission to use % has been denied%'
                    THEN regexp_extract(content, 'Permission to use (\\w+)', 1)
                WHEN content LIKE '%The user doesn''t want to proceed%'
                    THEN 'user_rejected'
                WHEN content LIKE 'Hook PreToolUse:% denied this tool%'
                    THEN 'hook_denied:' || regexp_extract(
                        content, 'Hook PreToolUse:(\\w+)', 1)
            END AS tool,
            count(*) AS denials
        FROM permission_denials
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY tool
        HAVING tool IS NOT NULL
        ORDER BY denials DESC
        LIMIT {limit};
    """)
    output(rows, args)


if __name__ == "__main__":
    main()
