import os
import re

from langchain_core.tools import tool

from agent.codebase_detective_tools.tools.code_item_tools import (JS_EXT, _files, _find_end, _is_test_path, _lines, reset_caches)

MAX_ENTRIES = 40

PRISMA_RE = re.compile(
    r"([A-Za-z_$][\w$.]*)\.([A-Za-z_$][\w$]*)\."
    r"(findMany|findFirst|findUnique|findFirstOrThrow|findUniqueOrThrow|count|aggregate|groupBy|"
    r"createMany|create|updateMany|update|upsert|deleteMany|delete)\s*\(")
RAW_RE = re.compile(r"\.(\$queryRaw(?:Unsafe)?|\$executeRaw(?:Unsafe)?)\s*(?=[(`])"
                    r"|\b(?:pool|client|db|conn|connection|pgPool)\.(query)\s*(?=\()")
LOOP_RE = re.compile(r"\bfor\s*(?:await\s*)?\(|\bwhile\s*\(|\.(?:forEach|map|flatMap|reduce)\s*\(")
DEF_ANY = re.compile(r"""^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:
      function\s*\*?\s*(?P<f>[A-Za-z_$][\w$]*)\s*[(<]
    | (?:const|let|var)\s+(?P<v>[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:function\b|\(|[A-Za-z_$][\w$]*\s*=>)
    | (?:module\.)?exports\.(?P<e>[A-Za-z_$][\w$]*)\s*=
    | (?:(?:public|private|protected|static|get|set)\s+)*(?P<m>[A-Za-z_$][\w$]*)\s*\([^;]*\{\s*$
)""", re.X)
_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return"}


def _balanced(text: str, start: int) -> str:
    """Text inside the bracket opened at text[start] (string/comment aware)."""
    depth, quote, i, n = 0, None, start, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == "/" and text[i + 1:i + 2] == "/":
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
        elif ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return text[start + 1:]


def _split_top(obj: str) -> list[str]:
    parts, cur, depth, quote, i = [], [], 0, None, 0
    while i < len(obj):
        ch = obj[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(obj):
                i += 1
                cur.append(obj[i])
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
            cur.append(ch)
        elif ch in "({[":
            depth += 1
            cur.append(ch)
        elif ch in ")}]":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _items(obj: str) -> dict:
    out = {}
    for p in _split_top(obj):
        p = p.strip()
        if p.startswith("..."):
            out["...spread"] = p
        elif ":" in p:
            k, v = p.split(":", 1)
            out[k.strip()] = v.strip()
        elif p:
            out[p] = p
    return out


def _enclosing(lines, idx):
    for j in range(idx, max(-1, idx - 250), -1):
        m = DEF_ANY.match(lines[j])
        if not m:
            continue
        name = m.group("f") or m.group("v") or m.group("e") or m.group("m")
        if name in _KEYWORDS:
            continue
        if _find_end(lines, j) >= idx:
            return name, j
    return "(top level)", None


def _loop_context(lines, idx, col, func_start):
    start = func_start if func_start is not None else max(0, idx - 80)
    hit = None
    for j in range(start, idx + 1):
        for m in LOOP_RE.finditer(lines[j]):
            if j == idx and m.start() >= col:
                continue
            if _find_end(lines, j) >= idx:
                hit = (m.group(0).strip(" ("), j + 1)     # innermost = last found
    return hit


def _describe_prisma(method: str, inner: str) -> str:
    inner = inner.strip()
    items = _items(_balanced(inner, 0)) if inner.startswith("{") else {}
    parts = []
    if method not in ("create", "createMany", "upsert"):
        w = items.get("where")
        if w and w.strip().startswith("{"):
            keys = [k for k in _items(_balanced(w.strip(), 0))]
            parts.append("where(" + ",".join(keys) + ")")
        elif w:
            parts.append("where(<expression>)")
        elif method in ("findMany", "count", "aggregate", "groupBy", "updateMany", "deleteMany"):
            parts.append("no where")
    if method == "findMany" and "take" not in items:
        parts.append("no take (unbounded)")
    n_inc = len(re.findall(r"\binclude\s*:", inner))
    if n_inc:
        parts.append(f"include x{n_inc}")
    if re.search(r"\bselect\s*:", inner):
        parts.append("select")
    if "orderBy" in items:
        parts.append("orderBy")
    return " ".join(parts)


def _describe_raw(rest: str, col: int) -> str:
    if rest[col] == "`":                                   # tagged template: up to the closing backtick
        end = rest.find("`", col + 1)
        text = rest[col + 1:end if end != -1 else col + 400]
    else:                                                  # call: first argument only
        args = _split_top(_balanced(rest, col))
        text = args[0].strip().strip("'\"`") if args else ""
    sql = " ".join(text.split())
    flags = []
    if re.search(r"select\s+\*", sql, re.I):
        flags.append("SELECT *")
    if re.search(r"\bselect\b", sql, re.I) and not re.search(r"\bwhere\b", sql, re.I):
        flags.append("no WHERE")
    if re.search(r"like\s+'%", sql, re.I):
        flags.append("leading-wildcard LIKE")
    return f'sql: "{sql[:110]}"' + (f" [{', '.join(flags)}]" if flags else "")


@tool
def scan_db_calls(path_hint: str = "", model: str = "") -> str:
    """
    List every database call in the codebase (or one area of it) in a single
    call: file:line, enclosing function, Prisma model.method (or raw SQL),
    filter keys, and measurable facts read off the code: whether the call is
    inside a loop / .map() (and which line starts that loop), findMany with
    no take, no where, include depth, SELECT * and similar. Covers Prisma
    client calls and raw SQL via $queryRaw / pool.query / db.query. Filter with
    path_hint (e.g. 'orders') and/or model (e.g. 'order'). Test files skipped.
    """
    reset_caches()
    hint = path_hint.lower().replace("\\", "/")
    model_f = model.lower()
    entries = []
    for rel, ext in _files():
        if ext not in JS_EXT:
            continue
        relp = rel.replace(os.sep, "/")
        if hint and hint not in relp.lower():
            continue
        if _is_test_path(rel) and "test" not in hint:
            continue
        lines = _lines(rel)
        for idx, line in enumerate(lines):
            s = line.lstrip()
            if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
                continue
            rest = "\n".join(lines[idx:idx + 30])
            for m in PRISMA_RE.finditer(line):
                if model_f and m.group(2).lower() != model_f:
                    continue
                inner = _balanced(rest, m.end() - 1)
                entries.append((relp, idx, m.start(), f"{m.group(2)}.{m.group(3)}",
                                _describe_prisma(m.group(3), inner)))
            if not model_f or model_f == "(raw)":
                for m in RAW_RE.finditer(line):
                    col = m.end()
                    entries.append((relp, idx, m.start(), f"raw.{m.group(1) or m.group(2)}",
                                    _describe_raw(rest, col) if col < len(rest) else ""))

    if not entries:
        return ("No Prisma or raw-SQL database calls found"
                f"{' under ' + repr(path_hint) if path_hint else ''}. "
                "Other ORMs (Sequelize, TypeORM, Mongoose) are not scanned.")

    entries.sort(key=lambda e: (e[0], e[1], e[2]))
    out, in_loop, unbounded = [], 0, 0
    cur_file = None
    for relp, idx, col, call, desc in entries[:MAX_ENTRIES]:
        lines = _lines(relp.replace("/", os.sep))
        fn, fstart = _enclosing(lines, idx)
        loop = _loop_context(lines, idx, col, fstart)
        if loop:
            in_loop += 1
        if "no take" in desc:
            unbounded += 1
        if relp != cur_file:
            out.append(relp)
            cur_file = relp
        flag = f"  INSIDE LOOP ({loop[0]} at line {loop[1]})" if loop else ""
        out.append(f"  :{idx + 1}  {fn}()  {call}  {desc}{flag}")
    out.append(f"-- {len(entries)} calls found" + (f" (first {MAX_ENTRIES} shown)" if len(entries) > MAX_ENTRIES else "")
               + f"; {in_loop} inside loops; {unbounded} findMany without take")
    return "\n".join(out)
