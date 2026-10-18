"""
Manual smoke test, no LLM involved. Fill in .env, edit the names below to match
your project, then run:  python test_tools.py
Every section should print real output, not an ERROR line.
"""
from tools import (view_code_item, scan_db_calls, profile_endpoint, explain_query,
                   inspect_table, function_history, db_activity, read_file, search_code)

FUNCTION = "getOrders"                       # a real function in your project
TABLE = "Order"                              # exact Postgres table name
ENDPOINT = "/api/orders?userId=1"            # a real GET path (include any /api prefix)
SQL = 'SELECT * FROM "Order" WHERE "userId" = 1'

for title, tool, args in [
    ("view_code_item (follow=2)", view_code_item, {"name": FUNCTION, "follow": 2}),
    ("scan_db_calls", scan_db_calls, {}),
    ("function_history", function_history, {"name": FUNCTION}),
    ("inspect_table", inspect_table, {"table_name": TABLE}),
    ("explain_query", explain_query, {"sql": SQL}),
    ("db_activity", db_activity, {}),
    ("profile_endpoint (takes ~12 s)", profile_endpoint, {"path": ENDPOINT, "runs": 3}),
    ("search_code", search_code, {"pattern": "findMany"}),
]:
    print(f"\n======== {title}\n{tool.invoke(args)}")
