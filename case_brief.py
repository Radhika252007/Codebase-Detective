"""
build_case_brief() -- a plain Python function, NOT an LLM tool.

Run it ONCE before the investigation starts and put the returned string in
the agent's state/prompt. It costs zero investigation steps because no LLM
decides to call it.

Design rules:
  * Facts only. It says what exists (tables, indexes, row counts), never what
    looks wrong -- diagnosing is the agent's job, and a brief that hints at the
    answer makes your planted-bug evaluation meaningless.
  * Every section fails independently into "(unavailable: reason)".
  * Small and capped, because it is sent with every prompt.
  * Never reads .env or file contents other than package.json / schema.prisma.
"""
import json
import os
import re
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from psycopg2 import sql

from config import PROJECT_ROOT, DATABASE_URL
from agent.codebase_detective_tools.tools.code_item_tools import SKIP_DIRS, JS_EXT
from agent.codebase_detective_tools.tools.db_tools import _connect

MAX_STACK_CHARS = 1200
MAX_STRUCTURE_CHARS = 2500
MAX_SCHEMA_CHARS = 3800
MAX_COLS_PER_TABLE = 14
MAX_TABLES = 40
COUNT_CAP = 100_000

STACK_HINTS = [
    ("express", "Express"), ("fastify", "Fastify"), ("koa", "Koa"),
    ("@nestjs/core", "NestJS"), ("@prisma/client", "Prisma"),
    ("sequelize", "Sequelize"), ("typeorm", "TypeORM"), ("mongoose", "Mongoose"),
    ("knex", "Knex"), ("pg", "node-postgres"), ("ioredis", "ioredis"),
    ("redis", "redis client"), ("bullmq", "BullMQ"), ("bull", "Bull"),
    ("socket.io", "Socket.io"), ("jsonwebtoken", "JWT"), ("passport", "Passport"),
    ("winston", "winston (logging)"), ("pino", "pino (logging)"),
    ("morgan", "morgan (request logging)"), ("typescript", "TypeScript"),
    ("jest", "Jest"), ("vitest", "Vitest"), ("mocha", "Mocha"),
]
ENTRY_CANDIDATES = [
    "server.js", "app.js", "index.js", "src/server.js", "src/app.js", "src/index.js",
    "server.ts", "src/server.ts", "src/app.ts", "src/index.ts", "src/main.ts",
]
CODE_EXT = JS_EXT | {".prisma", ".sql"}
TYPE_SHORT = {
    "integer": "int", "bigint": "bigint", "smallint": "smallint",
    "character varying": "varchar", "text": "text", "boolean": "bool",
    "double precision": "float", "numeric": "numeric", "real": "float",
    "timestamp without time zone": "timestamp", "timestamp with time zone": "timestamptz",
    "USER-DEFINED": "enum", "jsonb": "jsonb", "json": "json", "uuid": "uuid",
}


# ---------------------------------------------------------------- helpers
def _scrub(e: Exception) -> str:
    msg = str(e).strip().replace("\n", " ")
    if DATABASE_URL:
        msg = msg.replace(DATABASE_URL, "<db-url>")
    return f"{type(e).__name__}: {msg[:200]}"


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "\n... [section truncated]"


def _section(title: str, fn, limit: int) -> str:
    try:
        body = fn()
    except Exception as e:  # one broken section must never stop the investigation
        body = f"(unavailable: {_scrub(e)})"
    return f"## {title}\n{_cap(body, limit)}"


def _fmt_rows(n: int, exact: bool) -> str:
    if exact and n >= COUNT_CAP:
        return f"{COUNT_CAP // 1000}k+"
    s = str(n) if n < 1000 else (f"{n / 1000:.1f}k" if n < 1_000_000 else f"{n / 1_000_000:.1f}M")
    s = s.replace(".0k", "k").replace(".0M", "M")
    return s if (exact or n < 1000) else "~" + s


# ------------------------------------------------------------- tech stack
def _stack() -> str:
    root = Path(PROJECT_ROOT).resolve()
    pkg_path = root / "package.json"
    if not pkg_path.exists():
        return "No package.json at the project root (not a Node project, or code lives in a subfolder)."
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

    found = []
    for dep, label in STACK_HINTS:
        if dep in deps:
            found.append(f"{label} {str(deps[dep]).lstrip('^~>=')}")

    lang = "TypeScript" if ((root / "tsconfig.json").exists() or "typescript" in deps) else "JavaScript"
    pm = next((name for f, name in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                                    ("package-lock.json", "npm")) if (root / f).exists()), "unknown")
    entries = [e for e in ENTRY_CANDIDATES if (root / e).exists()]
    scripts = pkg.get("scripts", {})
    script_txt = ", ".join(f"{k}={scripts[k]}" for k in ("start", "dev") if k in scripts)

    lines = [
        f"Project: {pkg.get('name', '(unnamed)')} | Language: {lang} | Package manager: {pm}",
        "Dependencies detected: " + (", ".join(found) if found else "none of the known frameworks"),
        f"package.json main: {pkg.get('main', '(not set)')} | Entry files present: "
        + (", ".join(entries) if entries else "none of the usual names"),
    ]
    if script_txt:
        lines.append(f"Scripts: {script_txt}")
    return "\n".join(lines)


# ---------------------------------------------------------- source layout
def _structure(max_dirs: int = 45, max_files: int = 10, max_depth: int = 5) -> str:
    root = Path(PROJECT_ROOT).resolve()
    lines = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > max_depth:
            dirnames[:] = []
            continue
        files = sorted(f for f in filenames
                       if os.path.splitext(f)[1] in CODE_EXT and not f.endswith(".min.js"))
        if files:
            shown = files[:max_files]
            extra = len(files) - len(shown)
            label = "./" if rel == "." else rel.replace(os.sep, "/") + "/"
            lines.append(f"{label}: {', '.join(shown)}" + (f" (+{extra} more)" if extra else ""))
        if len(lines) >= max_dirs:
            lines.append("... (directory listing truncated)")
            break
    return "\n".join(lines) if lines else "(no source files found)"


# ------------------------------------------------------- schema: live DB
COLUMNS_SQL = """
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name <> '_prisma_migrations'
ORDER BY table_name, ordinal_position
"""
# pg_catalog (not information_schema): information_schema hides constraints
# from users that only have SELECT -- exactly what the read-only user has.
CONSTRAINTS_SQL = """
SELECT t.relname, c.contype::text,
       (SELECT array_agg(a.attname ORDER BY k.ord)
          FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum),
       rt.relname
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
LEFT JOIN pg_class rt ON rt.oid = c.confrelid
WHERE n.nspname = 'public' AND c.contype IN ('p', 'f')
"""
INDEXES_SQL = """
SELECT t.relname, pg_get_indexdef(x.indexrelid), x.indisprimary, x.indisunique
FROM pg_index x
JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public'
ORDER BY t.relname, x.indexrelid
"""
TABLES_SQL = """
SELECT c.relname, c.reltuples::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname <> '_prisma_migrations'
ORDER BY c.relname
"""


def _index_desc(indexdef: str, unique: bool) -> str:
    base = indexdef.split(" WHERE ")[0]
    m = re.search(r"USING\s+\w+\s+\((.*)\)\s*$", base, re.S)
    cols = m.group(1).replace('"', "").replace(" ", "") if m else "?"
    return ("unique" if unique else "") + f"({cols})" + (" partial" if " WHERE " in indexdef else "")


def _schema_from_db() -> str:
    with closing(_connect()) as conn, conn.cursor() as cur:
        cur.execute(COLUMNS_SQL); cols = cur.fetchall()
        cur.execute(CONSTRAINTS_SQL); cons = cur.fetchall()
        cur.execute(INDEXES_SQL); idx = cur.fetchall()
        cur.execute(TABLES_SQL); tables = cur.fetchall()[:MAX_TABLES]
        counts = {}
        for name, est in tables:
            if est > 0:
                counts[name] = (est, False)
            else:  # never analyzed (0 / -1): bounded exact count instead of a full scan
                cur.execute(sql.SQL("SELECT count(*) FROM (SELECT 1 FROM {} LIMIT %s) s")
                            .format(sql.Identifier(name)), (COUNT_CAP,))
                counts[name] = (cur.fetchone()[0], True)

    t_cols, pk, fk, t_idx = defaultdict(list), defaultdict(set), defaultdict(dict), defaultdict(list)
    for t, c, typ in cols:
        t_cols[t].append((c, TYPE_SHORT.get(typ, typ)))
    for t, kind, names, ref in cons:
        for c in names or []:
            if kind == "p":
                pk[t].add(c)
            else:
                fk[t][c] = ref
    for t, d, is_pk, is_uq in idx:
        if not is_pk:
            t_idx[t].append(_index_desc(d, is_uq))

    out = []
    for name, _ in tables:
        parts = []
        for c, typ in t_cols[name][:MAX_COLS_PER_TABLE]:
            mark = (" PK" if c in pk[name] else "") + (f" FK->{fk[name][c]}" if c in fk[name] else "")
            parts.append(f"{c} {typ}{mark}")
        more = len(t_cols[name]) - MAX_COLS_PER_TABLE
        if more > 0:
            parts.append(f"+{more} more columns")
        n, exact = counts[name]
        out.append(f"{name} ({_fmt_rows(n, exact)} rows): {', '.join(parts)}\n"
                   f"    indexes besides PK: {'; '.join(t_idx[name]) or 'none'}")
    return "\n".join(out) if out else "(no tables in the public schema)"


# ------------------------------------------------- schema: schema.prisma
def _schema_from_prisma() -> str:
    root = Path(PROJECT_ROOT).resolve()
    path = None
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "schema.prisma" in filenames:
            path = Path(dirpath) / "schema.prisma"
            break
    if path is None:
        raise FileNotFoundError("no schema.prisma found")

    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.findall(r"^model\s+(\w+)\s*\{(.*?)^\}", text, re.S | re.M)
    models = {n for n, _ in blocks}
    out = []
    for name, body in blocks[:MAX_TABLES]:
        lines = [l.split("//")[0].strip() for l in body.splitlines()]
        lines = [l for l in lines if l]
        fk, attrs, scalars = {}, [], []
        for l in lines:
            tok = l.split()
            if l.startswith("@@"):
                attrs.append(l)
            elif len(tok) >= 2 and tok[1].rstrip("?").replace("[]", "") in models:
                m = re.search(r"fields:\s*\[([^\]]*)\]", l)  # relation field -> FK on scalar
                if m:
                    for c in m.group(1).split(","):
                        fk[c.strip()] = tok[1].rstrip("?").replace("[]", "")
            elif len(tok) >= 2:
                scalars.append((tok[0], tok[1], l))
        parts = []
        for c, typ, l in scalars[:MAX_COLS_PER_TABLE]:
            mark = (" PK" if "@id" in l else "") + (" UNIQUE" if "@unique" in l else "") \
                   + (f" FK->{fk[c]}" if c in fk else "")
            parts.append(f"{c} {typ}{mark}")
        out.append(f"{name}: {', '.join(parts)}\n    declared: {'; '.join(attrs) or 'no @@index/@@unique'}")
    return "\n".join(out) if out else "(no models found in schema.prisma)"


def _schema() -> str:
    try:
        return "(source: live database)\n" + _schema_from_db()
    except Exception as db_err:
        reason = _scrub(db_err)
        try:
            return (f"(source: schema.prisma -- live DB unavailable: {reason}; "
                    "no row counts, and declared indexes may differ from real ones)\n"
                    + _schema_from_prisma())
        except Exception as file_err:
            return f"(unavailable: DB error: {reason}; schema.prisma: {_scrub(file_err)})"


# ----------------------------------------------------------------- public
def build_case_brief() -> str:
    """Return the pre-investigation brief as one prompt-ready string."""
    parts = [
        "# CASE BRIEF (auto-generated facts about the target project; contains no conclusions)",
        _section("Tech stack (inferred from package.json)", _stack, MAX_STACK_CHARS),
        _section("Source layout (directory: files)", _structure, MAX_STRUCTURE_CHARS),
        _section("Database schema", _schema, MAX_SCHEMA_CHARS),
        "Not included: API routes, logs, code. Explore with view_code_item "
        "(e.g. on the entry file) instead of guessing.",
    ]
    return "\n\n".join(parts)


if __name__ == "__main__":
    brief = build_case_brief()
    print(brief)
    print(f"\n[{len(brief)} characters]")