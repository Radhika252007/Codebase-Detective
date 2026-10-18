from contextlib import closing

from langchain_core.tools import tool
from psycopg2 import sql

from .db_tools import _connect

COUNT_CAP = 100_000


def _bytes(n) -> str:
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _when(v) -> str:
    return str(v)[:16] if v else "never"


def _rows_text(cur, relname: str, live: int, est: int) -> str:
    if live and live > 0:
        return f"~{live:,} rows (stats estimate)"
    if est and est > 0:
        return f"~{est:,} rows (planner estimate)"
    cur.execute(sql.SQL("SELECT count(*) FROM (SELECT 1 FROM {} LIMIT %s) s")
                .format(sql.Identifier(relname)), (COUNT_CAP,))
    n = cur.fetchone()[0]
    return f"{n:,}{'+' if n >= COUNT_CAP else ''} rows (counted)"


def _colset(cur, sql_text: str, params) -> list:
    cur.execute(sql_text, params)
    return cur.fetchall()


_COLS = ("(SELECT string_agg(a.attname, ',' ORDER BY k.ord) "
         "FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) "
         "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum)")


@tool
def inspect_table(table_name: str) -> str:
    """
    Everything useful about ONE table in a single call: row count, table and
    index sizes, every index with its size and how many times it has been
    used (idx_scan), sequential vs index scan counts, dead rows and last
    ANALYZE/VACUUM, foreign keys in both directions, and per-column
    statistics (distinct values, null share) from ANALYZE. Use the exact
    Postgres table name (Prisma models keep their case, e.g. 'Order').
    """
    if not table_name or len(table_name) > 63:
        return "ERROR: give a table name (max 63 characters)."
    try:
        with closing(_connect()) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT c.oid, c.relname, c.reltuples::bigint,
                       pg_relation_size(c.oid), pg_indexes_size(c.oid)
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
                  AND lower(c.relname) = lower(%s)
                ORDER BY (c.relname = %s) DESC LIMIT 1""", (table_name, table_name))
            row = cur.fetchone()
            if not row:
                names = [r[0] for r in _colset(cur, """
                    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY 1 LIMIT 40""", None)]
                return f"ERROR: no table '{table_name}'. Tables in public: {', '.join(names)}"
            oid, rel, est, tsize, isize = row

            st = _colset(cur, """
                SELECT seq_scan, seq_tup_read, COALESCE(idx_scan, 0), COALESCE(idx_tup_fetch, 0),
                       n_live_tup, n_dead_tup, GREATEST(last_analyze, last_autoanalyze),
                       GREATEST(last_vacuum, last_autovacuum)
                FROM pg_stat_user_tables WHERE relid = %s""", (oid,))
            idx = _colset(cur, """
                SELECT i.indexrelname, pg_get_indexdef(i.indexrelid), pg_relation_size(i.indexrelid),
                       i.idx_scan, x.indisunique, x.indisprimary
                FROM pg_stat_user_indexes i JOIN pg_index x ON x.indexrelid = i.indexrelid
                WHERE i.relid = %s ORDER BY i.indexrelname""", (oid,))
            fk_out = _colset(cur, f"""
                SELECT {_COLS}, rt.relname FROM pg_constraint c
                JOIN pg_class rt ON rt.oid = c.confrelid
                WHERE c.conrelid = %s AND c.contype = 'f'""", (oid,))
            fk_in = _colset(cur, f"""
                SELECT t.relname, {_COLS} FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                WHERE c.confrelid = %s AND c.contype = 'f'""", (oid,))
            stats = _colset(cur, """
                SELECT attname, n_distinct, null_frac FROM pg_stats
                WHERE schemaname = 'public' AND tablename = %s ORDER BY attname""", (rel,))
            live = st[0][4] if st else 0
            rows_text = _rows_text(cur, rel, live, est)
    except Exception as e:
        return f"ERROR inspecting table: {e}"

    out = [f'Table "{rel}" -- {rows_text}, table {_bytes(tsize)}, indexes {_bytes(isize)}']
    if st:
        s = st[0]
        out.append(f"Access since stats reset: seq scans {s[0]:,} (read {s[1]:,} rows), "
                   f"index scans {s[2]:,} (fetched {s[3]:,} rows)")
        out.append(f"Maintenance: dead rows {s[5]:,}, last analyze {_when(s[6])}, last vacuum {_when(s[7])}")
    out.append("Indexes:")
    for name, d, size, scans, uq, pk in idx:
        cols = d.split(" WHERE ")[0]
        cols = cols[cols.find("(", cols.find("USING")):] if "USING" in cols else "(?)"
        flag = " PK" if pk else (" UNIQUE" if uq else "")
        out.append(f"  {name} {cols.replace(chr(34), '')}{flag} -- {_bytes(size)}, used {scans or 0:,} times")
    if not idx:
        out.append("  none")
    if fk_out:
        out.append("Foreign keys: " + "; ".join(f"{c} -> {t}" for c, t in fk_out))
    if fk_in:
        out.append("Referenced by: " + "; ".join(f"{t}.{c}" for t, c in fk_in))
    if stats:
        parts = []
        for name, nd, nf in stats[:16]:
            if nd is None:
                d = "?"
            elif nd < 0:
                d = f"{-nd * 100:.0f}% of rows distinct"
            else:
                d = f"{int(nd)} distinct"
            parts.append(f"{name}: {d}, {nf * 100:.0f}% null")
        out.append("Column stats (from last ANALYZE): " + " | ".join(parts))
    else:
        out.append("Column stats: none (table never analyzed)")
    return "\n".join(out)[:4000]


@tool
def db_activity() -> str:
    """
    Snapshot of database health in one call: connections in use vs
    max_connections, sessions by state (active / idle / idle in transaction),
    sessions waiting on locks, longest-running queries, deadlocks, cache hit
    ratio, temp-file spills, and the key memory/timeout settings. Use it for
    suspected connection-pool exhaustion, lock waits or memory spills.
    Per-session detail from other users needs: GRANT pg_monitor TO <user>.
    """
    try:
        with closing(_connect()) as conn, conn.cursor() as cur:
            settings = dict(_colset(cur, """
                SELECT name, setting || COALESCE(unit, '') FROM pg_settings WHERE name IN
                ('max_connections', 'shared_buffers', 'work_mem', 'effective_cache_size',
                 'statement_timeout', 'idle_in_transaction_session_timeout', 'random_page_cost')""", None))
            total = _colset(cur, """SELECT count(*) FROM pg_stat_activity
                                    WHERE backend_type = 'client backend'""", None)[0][0]
            by_state = _colset(cur, """
                SELECT state, count(*), max(EXTRACT(EPOCH FROM (now() - state_change)))::int
                FROM pg_stat_activity
                WHERE backend_type = 'client backend' AND datname = current_database()
                  AND pid <> pg_backend_pid()
                GROUP BY state ORDER BY 2 DESC""", None)
            waiting = _colset(cur, "SELECT count(*) FROM pg_locks WHERE NOT granted", None)[0][0]
            slow = _colset(cur, """
                SELECT EXTRACT(EPOCH FROM (now() - query_start))::numeric(10,1), left(query, 140)
                FROM pg_stat_activity
                WHERE state = 'active' AND pid <> pg_backend_pid() AND datname = current_database()
                ORDER BY query_start LIMIT 3""", None)
            db = _colset(cur, """
                SELECT numbackends, xact_commit, xact_rollback, deadlocks, blks_hit, blks_read,
                       temp_files, temp_bytes FROM pg_stat_database WHERE datname = current_database()""", None)
    except Exception as e:
        return f"ERROR reading activity: {e}"

    out = [f"Connections: {total} client sessions open, max_connections = {settings.get('max_connections', '?')}"]
    hidden = sum(c for s, c, _ in by_state if s is None)
    shown = [(s, c, age) for s, c, age in by_state if s is not None]
    if shown:
        out.append("Other sessions by state: " + ", ".join(
            f"{s} {c} (oldest in state {age}s)" for s, c, age in shown))
    if hidden:
        out.append(f"{hidden} sessions belong to other users and their state is hidden "
                   "(run: GRANT pg_monitor TO <this db user>)")
    if not by_state:
        out.append("No other sessions connected right now.")
    out.append(f"Ungranted lock requests right now: {waiting}")
    if slow:
        out.append("Longest running active queries: " + " | ".join(f"{a}s: {_short(q)}" for a, q in slow))
    if db:
        nb, com, rb, dl, hit, rd, tf, tb = db[0]
        ratio = f"{hit * 100 / (hit + rd):.1f}%" if (hit + rd) else "n/a"
        out.append(f"Database totals since stats reset: commits {com:,}, rollbacks {rb:,}, deadlocks {dl}, "
                   f"cache hit ratio {ratio}, temp files {tf} ({_bytes(tb)})")
    out.append("Settings: " + ", ".join(f"{k}={v}" for k, v in settings.items() if k != "max_connections"))
    return "\n".join(out)[:3000]


def _short(s, n=110):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."