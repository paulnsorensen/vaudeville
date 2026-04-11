#!/usr/bin/env python3
"""Detect bash commands that should use dedicated tools instead.

Usage: python3 tool_misuse.py [--days 14] [--limit 15] [--json]
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
                WHEN bash_cmd LIKE 'cat %' OR bash_cmd LIKE 'head %'
                    OR bash_cmd LIKE 'tail %' THEN 'cat/head/tail -> Read'
                WHEN bash_cmd LIKE 'grep %' OR bash_cmd LIKE 'rg %'
                    OR bash_cmd LIKE 'egrep %' THEN 'grep/rg -> Grep'
                WHEN bash_cmd LIKE 'find %' OR bash_cmd LIKE 'fd %'
                    THEN 'find/fd -> Glob'
                WHEN bash_cmd LIKE 'sed %' OR bash_cmd LIKE '%sed -i%'
                    THEN 'sed -> Edit'
                WHEN bash_cmd LIKE 'echo %>>%' OR bash_cmd LIKE 'echo %>%'
                    OR (bash_cmd LIKE 'cat %' AND bash_cmd LIKE '%>%')
                    THEN 'echo/cat redirect -> Write'
            END AS misuse_type,
            count(*) AS uses
        FROM tool_uses
        WHERE tool_name = 'Bash'
          AND bash_cmd IS NOT NULL
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY misuse_type
        HAVING misuse_type IS NOT NULL
        ORDER BY uses DESC
        LIMIT {limit};
    """)
    output(rows, args)


if __name__ == "__main__":
    main()
