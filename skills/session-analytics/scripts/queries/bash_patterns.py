#!/usr/bin/env python3
"""Most-repeated bash commands, optionally filtered to dangerous ones.

Usage: python3 bash_patterns.py [--days 14] [--limit 15] [--dangerous] [--json]
"""

import sys
from _db import output, parse_days, parse_limit, query


def main() -> None:
    args = sys.argv[1:]
    days = parse_days(args)
    limit = parse_limit(args)
    dangerous_only = "--dangerous" in args

    danger_filter = (
        """
          AND (
            bash_cmd LIKE '%rm -rf%'
            OR (bash_cmd LIKE '%git push%--force%'
                AND bash_cmd NOT LIKE '%--force-with-lease%')
            OR bash_cmd LIKE '%DROP %'
            OR bash_cmd LIKE '%> /dev/%'
            OR bash_cmd LIKE '%chmod 777%'
            OR bash_cmd LIKE '%--no-verify%'
          )
    """
        if dangerous_only
        else ""
    )

    rows = query(f"""
        SELECT
            bash_cmd,
            count(*) AS uses
        FROM tool_uses
        WHERE tool_name = 'Bash'
          AND bash_cmd IS NOT NULL
          AND length(bash_cmd) > 10
          AND timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
          {danger_filter}
        GROUP BY bash_cmd
        ORDER BY uses DESC
        LIMIT {limit};
    """)
    output(rows, args)


if __name__ == "__main__":
    main()
