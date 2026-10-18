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
1. `pip install -r requirements.txt`; copy `.env.example` to `.env` (`TARGET_PROJECT_ROOT`, `TARGET_DATABASE_URL`, `TARGET_BASE_URL`, optional `TARGET_AUTH_TOKEN`).
2. Read-only DB user (the real safety boundary):
```sql
CREATE USER detective_ro WITH PASSWORD '...';
GRANT CONNECT ON DATABASE yourdb TO detective_ro;
GRANT USAGE ON SCHEMA public TO detective_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO detective_ro;
GRANT pg_read_all_stats TO detective_ro;   -- lets db_activity / pg_stat_statements see the app's sessions
```
3. Optional, for exact "queries per request": in postgresql.conf set `shared_preload_libraries = 'pg_stat_statements'`, restart, then `CREATE EXTENSION pg_stat_statements;` (superuser).
4. `python test_tools.py` (edit the names at the top first) and `python case_brief.py`.

## Known limits
- `profile_endpoint` takes ~12 s: Postgres publishes table counters up to 10 s late, so it waits once. Counts include any other traffic on the database in that window.
- Static scanners (`view_code_item`, `scan_db_calls`) are regex + bracket matching: JS/TS and `.prisma` only, Prisma + raw SQL only (no Sequelize/TypeORM/Mongoose), regex literals with brackets can confuse the end-of-function finder, multi-line method signatures are missed.
- `function_history` needs the project inside a git repo.
