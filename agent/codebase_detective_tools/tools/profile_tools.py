import os
import statistics
import time
from contextlib import closing

import requests
from langchain_core.tools import tool
from psycopg2 import sql

from config import TARGET_BASE_URL, AUTH_TOKEN
from .db_tools import _connect
from .route_tools import check_route

MAX_RUNS = 10
STATS_WAIT = 11.0          # Postgres publishes table counters from an idle backend up to 10 s late


def _err(e: Exception) -> str:
    return f"{type(e).__name__}: {' '.join(str(e).split())[:160]}"


def _snapshot():
    """(per-table counters, per-statement counters or None, note)."""
    with closing(_connect()) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT relname, seq_scan, seq_tup_read,
                                  COALESCE(idx_scan, 0), COALESCE(idx_tup_fetch, 0)
                           FROM pg_stat_user_tables""")
            tables = {r[0]: tuple(int(x or 0) for x in r[1:]) for r in cur.fetchall()}
            cur.execute("""SELECT n.nspname FROM pg_extension e
                           JOIN pg_namespace n ON n.oid = e.extnamespace
                           WHERE e.extname = 'pg_stat_statements'""")
            ext = cur.fetchone()
            if not ext:
                return tables, None, "pg_stat_statements is not installed"
            schema = ext[0]          # 'public' locally, usually 'extensions' on Supabase
            try:
                cur.execute(sql.SQL("""SELECT queryid, calls, total_exec_time, left(query, 160)
                                       FROM {}.pg_stat_statements
                                       WHERE dbid = (SELECT oid FROM pg_database
                                                     WHERE datname = current_database())""")
                            .format(sql.Identifier(schema)))
                rows = cur.fetchall()
            except Exception as e:
                conn.rollback()
                return tables, None, (f"pg_stat_statements unreadable ({type(e).__name__}); try: "
                                      f"GRANT USAGE ON SCHEMA {schema} TO <this db user>")
            if any(r[0] is None for r in rows):
                return tables, None, "pg_stat_statements hides other users' queries (GRANT pg_monitor TO <this db user>)"
            return tables, {r[0]: (int(r[1]), float(r[2]), r[3]) for r in rows}, None


def _format_db_delta(before, after, runs: int) -> str:
    tb, sb, note = before
    ta, sa, _ = after
    out = []
    if sb is not None and sa is not None:
        deltas = []
        for q, (calls, ms, text) in sa.items():
            if any(x in text for x in ("pg_stat_", "pg_extension", "pg_database")):
                continue
            pc, pm, _ = sb.get(q, (0, 0.0, ""))
            if calls - pc > 0:
                deltas.append((calls - pc, ms - pm, " ".join(text.split())))
        total = sum(d[0] for d in deltas)
        out.append(f"Queries per request: {total / runs:.1f} (pg_stat_statements)")
        for calls, ms, text in sorted(deltas, key=lambda d: -d[0])[:5]:
            out.append(f"  {calls / runs:.1f}x/request, {ms / calls:.2f} ms avg: {text[:120]}")
    else:
        out.append(f"Queries per request: unavailable -- {note}")
    changed = []
    for t, vals in ta.items():
        d = tuple(a - b for a, b in zip(vals, tb.get(t, (0, 0, 0, 0))))
        if any(d):
            changed.append((t, d))
    out.append("Table access per request:" if changed else "Table access per request: no table activity recorded")
    for t, d in sorted(changed, key=lambda x: -(x[1][1] + x[1][3]))[:8]:
        out.append(f'  "{t}": seq scans {d[0] / runs:.1f} (rows read {d[1] / runs:,.0f}), '
                   f"index scans {d[2] / runs:.1f} (rows fetched {d[3] / runs:,.0f})")
    return "\n".join(out)


def _item_count(resp):
    try:
        data = resp.json()
    except Exception:
        return None
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                return f"{len(v)} (in '{k}')"
    return None


def _bad_status(path, r) -> str:
    body = " ".join(r.text.split())[:200]
    hint = ""
    if r.status_code in (401, 403):
        hint = " The endpoint needs authentication: set TARGET_AUTH_TOKEN."
    elif r.status_code == 404 and "Cannot GET" in r.text:
        hint = (" Express has no route for this path (wrong path or mount prefix such as /api); "
                "read the entry file (named in the case brief) with read_file to find the mount prefix.")
    elif r.status_code == 404:
        hint = " The route exists but the handler itself returned 404 (e.g. record not found)."
    return f"GET {path} -> HTTP {r.status_code}.{hint}\nBody: {body}"


@tool
def profile_endpoint(path: str, runs: int = 5) -> str:
    """
    Profile one GET endpoint end to end in a single call. Sends one warmup
    request, then `runs` measured requests (max 10) and reports: latency
    (min/median/max and the cold first request), response size and item
    count, how many database queries each request ran (needs
    pg_stat_statements, else it says so), the slowest/most repeated queries,
    and per-table sequential vs index scan counts and rows read per request.
    Path may include a query string, e.g. '/api/tickets?userId=1'. GET only.
    Takes about 12 seconds, because Postgres publishes table counters up to 10 s late.
    """
    runs = max(1, min(int(runs), MAX_RUNS))
    if not path.startswith("/"):
        path = "/" + path
    if os.environ.get("TARGET_ROUTES_MANIFEST"):          # optional strict route check
        try:
            ok, info = check_route(path, "GET")
            if not ok:
                return f"ERROR (not sent): {info}"
        except FileNotFoundError:
            pass

    url = TARGET_BASE_URL.rstrip("/") + path
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"} if AUTH_TOKEN else {}
    session = requests.Session()

    def hit():
        t0 = time.monotonic()
        r = session.get(url, headers=headers, timeout=30)
        return (time.monotonic() - t0) * 1000, r

    before, db_note = None, None
    try:
        before = _snapshot()      # BEFORE the warmup, so its late-published counters land inside the window
    except Exception as e:
        db_note = f"database counters unavailable ({_err(e)})"

    try:
        warm_ms, r = hit()
    except Exception as e:
        return f"ERROR calling endpoint: {_err(e)}"
    if r.status_code >= 400:
        return _bad_status(path, r)

    times, codes, last = [], [], r
    for _ in range(runs):
        try:
            ms, last = hit()
        except Exception as e:
            return f"ERROR during measured runs: {_err(e)}"
        times.append(ms)
        codes.append(last.status_code)

    delta_text = db_note or ""
    if before is not None:
        time.sleep(STATS_WAIT)
        try:
            delta_text = _format_db_delta(before, _snapshot(), runs + 1)   # warmup + measured requests
        except Exception as e:
            delta_text = f"database counters unavailable ({_err(e)})"

    items = _item_count(last)
    out = [f"GET {path} -- {runs} measured runs after 1 warmup",
           f"Latency ms: min {min(times):.0f} | median {statistics.median(times):.0f} | "
           f"max {max(times):.0f} | first (cold) request {warm_ms:.0f}",
           f"Status: {sorted(set(codes))} | response {len(last.content):,} bytes"
           + (f" | items: {items}" if items is not None else ""),
           delta_text,
           "Note: database numbers are averaged over the warmup + measured requests and include any "
           "other traffic on this database during that window."]
    return "\n".join(out)