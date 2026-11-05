import json
import re
from contextlib import closing
import sqlparse
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from agent.database_access import connect_read_only, parse_database_tables
from agent.runtime_context import DetectiveContext
from config import MAX_EXPLAIN_CHARS

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CREATE)\b",
    re.IGNORECASE,
)


def _is_select_only(sql: str) -> bool:
    """
    Refuse anything that isn't a single read-only SELECT. Checked two ways
    -- statement type via sqlparse, and a keyword denylist -- because
    relying on either check alone is easier to get wrong on edge cases
    (e.g. multiple statements separated by ';', or a CTE that writes).
    This, plus the DB_URL being a read-only user (see README), is the
    actual safety boundary: the agent may look, never touch.
    """
    parsed = sqlparse.parse(sql)
    if len(parsed) != 1:
        return False
    if parsed[0].get_type() != "SELECT":
        return False
    if _FORBIDDEN.search(sql):
        return False
    return True


def _connect(runtime: ToolRuntime[DetectiveContext] | None = None):
    context = runtime.context if runtime is not None else None
    if context is None:
        raise RuntimeError("No per-investigation database runtime is available.")
    if not runtime.state.get("inspect_database", False):
        raise RuntimeError("Database inspection is disabled for this investigation.")
    if runtime.state.get("database_access_status") != "ready":
        raise RuntimeError("Database access has not passed validation.")

    return connect_read_only(context.database_url, context.database_tables)


def _resolve_scoped_table(
    table_name: str,
    database_tables: tuple[str, ...],
) -> tuple[str, str] | None:
    if not isinstance(table_name, str):
        return None
    try:
        allowed_tables = parse_database_tables(database_tables)
    except ValueError:
        return None
    if "." in table_name:
        schema_name, relation_name = table_name.split(".", 1)
        candidate = (schema_name, relation_name)
        return candidate if candidate in allowed_tables else None

    matches = [table for table in allowed_tables if table[1] == table_name]
    return matches[0] if len(matches) == 1 else None


@tool
def run_explain(sql: str, runtime: ToolRuntime[DetectiveContext]) -> str:
    """
    Run EXPLAIN (ANALYZE, BUFFERS) on a read-only SELECT query against the
    target database and return the query plan as text. Use this to check
    whether a query does a sequential scan vs an index scan, how many rows
    it actually touches, and how long it actually takes. Only a single
    SELECT statement is allowed -- anything else is rejected before it
    reaches the database, and the DB connection itself is read-only too.
    """
    if not _is_select_only(sql):
        return "ERROR: only a single read-only SELECT statement is allowed."

    try:
        with closing(_connect(runtime)) as conn:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN (ANALYZE, BUFFERS) {sql}")
                rows = cur.fetchall()
    except Exception as e:
        return f"ERROR running EXPLAIN: {type(e).__name__}"

    plan = "\n".join(row[0] for row in rows)
    return plan[:MAX_EXPLAIN_CHARS]


@tool
def get_indexes(
    table_name: str,
    runtime: ToolRuntime[DetectiveContext],
) -> str:
    """
    List the indexes that exist on a given table in the target database
    (table_name should match the actual Postgres table name, e.g. 'Order'
    for a Prisma model called Order). Use this after run_explain shows a
    sequential scan, to check whether the filtered column actually has an
    index.
    """
    context = runtime.context
    scoped_table = _resolve_scoped_table(table_name, context.database_tables)
    if scoped_table is None:
        return "ERROR: table is not in the configured database allowlist."
    schema_name, relation_name = scoped_table
    query = """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s AND tablename = %s
        ORDER BY indexname;
    """
    try:
        with closing(_connect(runtime)) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (schema_name, relation_name))
                rows = cur.fetchall()
    except Exception as e:
        return f"ERROR fetching indexes: {type(e).__name__}"

    if not rows:
        return (
            f"No indexes found on table '{schema_name}.{relation_name}' "
            "(check the exact PostgreSQL table name)."
        )

    return "\n".join(f"{name}: {definition}" for name, definition in rows)


# ---------------------------------------------------------------- explain_query
def _plan_nodes(node, depth=0, out=None):
    out = [] if out is None else out
    out.append((depth, node))
    for child in node.get("Plans") or []:
        _plan_nodes(child, depth + 1, out)
    return out


def _short(s, n=90):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 3] + "..."


@tool
def explain_query(
    sql: str,
    runtime: ToolRuntime[DetectiveContext],
    raw: bool = False,
) -> str:
    """
    Run EXPLAIN (ANALYZE, BUFFERS) on a single read-only SELECT and return a
    compact summary instead of raw plan text: execution/planning time, one
    line per plan node (rows estimated vs actual, loops, time, filter), and a
    list of measured facts (sequential scans with rows read vs kept, row
    misestimates, nodes executed many times, sorts/hashes spilling to disk,
    buffer reads). raw=True returns the JSON plan instead. The query really
    executes (read-only, 15 s timeout), so use realistic parameter values.
    """
    if not _is_select_only(sql):
        return "ERROR: only a single read-only SELECT statement is allowed."
    try:
        with closing(_connect(runtime)) as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '15s'")
                cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
                data = cur.fetchone()[0]
    except Exception as e:
        return f"ERROR running EXPLAIN: {type(e).__name__}"
    if isinstance(data, str):
        data = json.loads(data)
    root = data[0]
    if raw:
        return json.dumps(root)[:MAX_EXPLAIN_CHARS]

    nodes = _plan_nodes(root["Plan"])
    lines, facts = [], []
    for depth, n in nodes:
        label = n["Node Type"]
        if n.get("Relation Name"):
            label += f' on "{n["Relation Name"]}"'
        if n.get("Index Name"):
            label += f' using {n["Index Name"]}'
        loops = n.get("Actual Loops", 1) or 1
        actual = n.get("Actual Rows", 0)
        est = n.get("Plan Rows", 0)
        total_ms = (n.get("Actual Total Time") or 0) * loops
        line = f'{"  " * depth}{label} | rows est {est} actual {actual}'
        if loops > 1:
            line += f" x{loops} loops"
        line += f" | {total_ms:.1f} ms"
        cond = n.get("Index Cond") or n.get("Filter") or n.get("Hash Cond") or n.get("Join Filter")
        if cond:
            line += f" | cond: {_short(cond, 70)}"
        removed = (n.get("Rows Removed by Filter") or 0) * loops
        if removed:
            line += f" | removed by filter {removed}"
        lines.append(line)

        rel = n.get("Relation Name")
        if "Seq Scan" in n["Node Type"] and rel:
            kept = actual * loops
            facts.append(f'Seq Scan on "{rel}": read {kept + removed} rows, kept {kept}'
                         + (f" (x{loops} loops)" if loops > 1 else ""))
        hi, lo = max(est, 1), max(actual, 1)
        if max(hi, lo) >= 100 and max(hi / lo, lo / hi) >= 10:
            facts.append(f"Row estimate off by {max(hi / lo, lo / hi):.0f}x at {label}: est {est}, actual {actual}")
        if loops >= 10:
            facts.append(f"{label} executed {loops} times ({total_ms:.1f} ms total)")
        if n.get("Sort Space Type") == "Disk":
            facts.append(f"Sort spilled to disk ({n.get('Sort Space Used')} kB)")
        if (n.get("Hash Batches") or 1) > 1:
            facts.append(f"Hash used {n['Hash Batches']} batches (spilled past work_mem)")

    top = root["Plan"]
    head = (f"Execution {root.get('Execution Time', 0):.1f} ms | Planning {root.get('Planning Time', 0):.1f} ms"
            f" | buffers: hit {top.get('Shared Hit Blocks', 0)}, read {top.get('Shared Read Blocks', 0)}")
    out = head + "\nPlan:\n" + "\n".join(lines)
    out += "\nMeasured facts:\n" + ("\n".join(f"- {f}" for f in facts) if facts else "- none flagged")
    return out[:MAX_EXPLAIN_CHARS]
