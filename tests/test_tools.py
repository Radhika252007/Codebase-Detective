"""
Manual sanity check for each tool, with NO LLM involved.

This is Milestone 1 from the project guide: "write the tools, test each
without any LLM." Run this after filling in .env. Edit the table/SQL
names below to match your actual Prisma models before running.

    python test_tools.py
"""
from agent.codebase_detective_tools.tools import (
    list_project_files,
    read_file,
    search_code,
    run_explain,
    get_indexes,
    time_endpoint,
)

print("=== list_project_files ===")
print(list_project_files.invoke({"relative_path": "."}))

print("\n=== search_code ('findMany') ===")
print(search_code.invoke({"pattern": "findMany"}))

# --- Edit these two to match a real table and a real, cheap query ---
TABLE_NAME = "Booking"            # exact Postgres table name, case-sensitive
SAMPLE_SQL = 'SELECT * FROM "Booking" LIMIT 10'

print(f"\n=== get_indexes('{TABLE_NAME}') ===")
print(get_indexes.invoke({"table_name": TABLE_NAME}))

print(f"\n=== run_explain ===")
print(run_explain.invoke({"sql": SAMPLE_SQL}))

print("\n=== time_endpoint('/bookings') ===")
print(time_endpoint.invoke({"path": "/bookings"}))

print("\nDone. Milestone 1 is complete once every section above returns")
print("real output instead of an ERROR line.")
