#!/usr/bin/env python3
"""Top tools by usage count.

Usage: python3 tool_usage.py [--days 14] [--limit 15] [--json]
"""

import sys
from _db import output, parse_days, parse_limit, query


def main() -> None:
    args = sys.argv[1:]
    days = parse_days(args)
    limit = parse_limit(args)

    rows = query(f"""
        SELECT
            tool_name,
            count(*) AS uses,
            count(DISTINCT sessionId) AS sessions
        FROM tool_uses
        WHERE timestamp::DATE >= CURRENT_DATE - INTERVAL '{days}' DAY
        GROUP BY tool_name
        ORDER BY uses DESC
        LIMIT {limit};
    """)
    output(rows, args)


if __name__ == "__main__":
    main()
