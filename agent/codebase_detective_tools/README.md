# Codebase Detective: tools + case brief

No LLM in here yet. `case_brief.py` runs once before an investigation; `tools/` is what the agent may call.

## The agent's tools (`tools.ALL_TOOLS`)

| Tool | Answers in one call | Replaces |
|---|---|---|
| `view_code_item(name, follow=1)` | Definition + what it calls + who calls it + the project functions it calls (controller -> service) | search_code + read_file chains |
| `scan_db_calls(path_hint, model)` | Every Prisma / raw-SQL call: file:line, function, filters, inside-a-loop, no `take`, include depth | repeated greps |
| `profile_endpoint(path, runs)` | Latency, response size/items, queries per request (needs pg_stat_statements), seq vs index scans per table | time_endpoint |
| `explain_query(sql)` | Parsed plan: per-node rows est/actual, loops, seq-scan rows read vs kept, spills | run_explain |
| `inspect_table(name)` | Rows, sizes, indexes (+usage), seq/index scan counts, FKs both ways, column stats | get_indexes |
| `function_history(name)` | `git log -L` for the function's exact lines + uncommitted changes | - |
| `db_activity()` | Connections vs max, session states, lock waits, cache hit ratio, temp spills, settings | - |
| `read_file`, `search_code` | fallbacks | - |

Tools report **measurements, never verdicts**; the agent does the diagnosing.
Retired but still importable: `list_project_files` (the case brief covers layout), `run_explain`, `get_indexes`, `time_endpoint`, `list_api_routes` (needed a manifest script).

## Setup
1. `pip install -r requirements.txt`.
2. Provide `project_root`, `database_url`, `database_tables`, `base_url`, `auth_token`, and `routes_manifest` through per-investigation runtime context. Keep the database password and API token in a secure credential store; do not put them in graph state or HITL responses.
3. If DB inspection is enabled, configure a dedicated, non-privileged read-only role with grants limited to the configured allowlist:
```sql
CREATE ROLE detective_ro LOGIN PASSWORD '<set securely>';
GRANT CONNECT ON DATABASE yourdb TO detective_ro;
GRANT USAGE ON SCHEMA app TO detective_ro;
GRANT SELECT ON TABLE app.bookings, app.users TO detective_ro;
```
Do not grant ownership, write privileges, broad data roles, or access outside the allowlist. `pg_monitor` and `pg_stat_statements` are optional enhancements; neither is required for basic inspection.
4. Optional, for exact "queries per request": in postgresql.conf set `shared_preload_libraries = 'pg_stat_statements'`, restart, then `CREATE EXTENSION pg_stat_statements;` (superuser).
5. Run the offline suite with `python -m unittest discover -s tests -p "test_*.py"`.

## Known limits
- `profile_endpoint` takes ~12 s: Postgres publishes table counters up to 10 s late, so it waits once. Counts include any other traffic on the database in that window.
- Static scanners (`view_code_item`, `scan_db_calls`) are regex + bracket matching: JS/TS and `.prisma` only, Prisma + raw SQL only (no Sequelize/TypeORM/Mongoose), regex literals with brackets can confuse the end-of-function finder, multi-line method signatures are missed.
- `function_history` needs the project inside a git repo.
