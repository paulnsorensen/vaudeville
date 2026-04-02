#!/usr/bin/env python3
"""Tools ranked by error rate.

Usage: python3 error_rates.py [--days 14] [--limit 15] [--min-uses 5] [--json]
"""

import sys
from _db import output, parse_days, parse_limit, query


def parse_min_uses(args: list[str], default: int = 5) -> int:
    for i, arg in enumerate(args):
        if arg == "--min-uses" and i + 1 < len(args):
            return int(args[i + 1])
    return default


def main() -> None:
    args = sys.argv[1:]
    days = parse_days(args)
    limit = parse_limit(args)
    min_uses = parse_min_uses(args)

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
        HAVING count(*) >= {min_uses}
        ORDER BY error_pct DESC
        LIMIT {limit};
    """)
    output(rows, args)


if __name__ == "__main__":
    main()
