import os
import re
from pathlib import Path

from langchain_core.tools import tool
from config import PROJECT_ROOT
from agent.codebase_detective_tools.tools.file_tools import _resolve_safe_path

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "coverage", ".turbo"}
JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
PRISMA_EXT = {".prisma"}

MAX_ITEMS = 3            # definitions returned for the requested name
MAX_REFS = 6             # call sites listed
MAX_CALLEES = 12         # names listed under "Calls"
MAX_SCAN_LINES = 400     # how far we look for the end of one definition
MAX_FILE_BYTES = 1_000_000
MAX_OUTPUT_CHARS = 9000
MAX_FOLLOWED = 6         # callee definitions inlined by follow=
MAX_FOLLOW_DEPTH = 2
FOLLOW_MAX_LINES = 60

_NAME_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_NOT_CALLS = {"if", "for", "while", "switch", "catch", "function", "return", "typeof",
              "require", "super", "import", "async", "await", "new"}
# Names never worth following: language/array/express helpers and ORM verbs.
_COMMON_METHODS = {
    "map", "filter", "forEach", "reduce", "find", "some", "every", "push", "pop", "shift",
    "join", "split", "slice", "splice", "includes", "indexOf", "then", "catch", "finally",
    "json", "send", "status", "next", "log", "error", "warn", "toString", "parseInt",
    "parseFloat", "Number", "String", "Boolean", "Date", "Object", "Array", "JSON", "Math",
    "Promise", "require", "set", "get", "has", "delete", "keys", "values", "entries",
    "assign", "stringify", "parse", "end", "redirect", "render", "create", "update",
    "findMany", "findFirst", "findUnique", "count", "upsert", "aggregate", "groupBy",
    "createMany", "updateMany", "deleteMany", "query", "$transaction", "$queryRaw",
}
_KIND_RANK = {"function": 0, "class": 0, "variable": 0, "exports": 0, "model": 0,
              "method": 1, "property": 1}

# Per-call caches: one investigation tool call walks the project once, not once per lookup.
_FILE_CACHE: dict[str, list[str]] = {}
_LIST_CACHE: list | None = None


def reset_caches() -> None:
    global _LIST_CACHE
    _FILE_CACHE.clear()
    _LIST_CACHE = None


def _lines(rel: str) -> list[str]:
    if rel not in _FILE_CACHE:
        try:
            _FILE_CACHE[rel] = (Path(PROJECT_ROOT).resolve() / rel).read_text(
                encoding="utf-8", errors="replace").splitlines()
        except OSError:
            _FILE_CACHE[rel] = []
    return _FILE_CACHE[rel]


def _js_patterns(n: str):
    return [
        ("function", re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*{n}\s*[(<]")),
        ("class", re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+{n}\b")),
        ("variable", re.compile(rf"^\s*(?:export\s+)?(?:const|let|var)\s+{n}\s*(?::[^=]+)?=")),
        ("exports", re.compile(rf"^\s*(?:module\.)?exports\.{n}\s*=")),
        ("method", re.compile(rf"^\s*(?:(?:public|private|protected|static|async|get|set)\s+)*{n}\s*\([^;]*\{{\s*$")),
        ("property", re.compile(rf"^\s*{n}\s*:\s*(?:async\s*)?(?:function\b|\()")),
    ]


def _prisma_patterns(n: str):
    return [("model", re.compile(rf"^\s*(?:model|enum|type)\s+{n}\s*\{{"))]


def _find_end(lines: list[str], start: int) -> int:
    """
    Index of the line where the definition starting at `start` ends. Counts
    ( [ { depth while skipping strings, template literals and comments.
    Known limits: regex literals containing brackets/quotes, odd formatting.
    """
    depth = 0
    opened = brace_seen = in_block = in_tpl = False
    last = min(len(lines), start + MAX_SCAN_LINES)
    for i in range(start, last):
        line = lines[i]
        quote = None
        j = 0
        while j < len(line):
            ch = line[j]
            nxt = line[j + 1] if j + 1 < len(line) else ""
            if in_block:
                if ch == "*" and nxt == "/":
                    in_block = False
                    j += 1
            elif in_tpl:
                if ch == "\\":
                    j += 1
                elif ch == "`":
                    in_tpl = False
            elif quote:
                if ch == "\\":
                    j += 1
                elif ch == quote:
                    quote = None
            elif ch == "/" and nxt == "/":
                break
            elif ch == "/" and nxt == "*":
                in_block = True
                j += 1
            elif ch in "'\"":
                quote = ch
            elif ch == "`":
                in_tpl = True
            elif ch in "{[(":
                depth += 1
                opened = True
                if ch == "{":
                    brace_seen = True
            elif ch in "}])":
                depth -= 1
            j += 1
        if in_block or in_tpl or not opened or depth > 0:
            continue
        if brace_seen or line.rstrip().endswith(";"):
            return i
        if i + 1 >= len(lines) or not lines[i + 1].strip():
            return i
    return last - 1


def _callees(body: str, own_name: str) -> list[str]:
    seen: list[str] = []
    for m in re.finditer(r"((?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*)\s*\(", body):
        full = m.group(1)
        first, last = full.split(".")[0], full.split(".")[-1]
        if first in _NOT_CALLS or last == own_name or full.startswith("console."):
            continue
        if full not in seen:
            seen.append(full)
        if len(seen) >= MAX_CALLEES:
            break
    # functions passed by reference, e.g. orders.map(formatOrder)
    for m in re.finditer(r"\.(?:map|filter|forEach|reduce|then|catch|find|some|every|flatMap|sort)"
                        r"\(\s*([A-Za-z_$][\w$]*)\s*[,)]", body):
        ref = m.group(1)
        if ref != own_name and ref not in _NOT_CALLS and ref not in seen and len(seen) < MAX_CALLEES:
            seen.append(ref)
    return seen


def _iter_files():
    root = Path(PROJECT_ROOT).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1]
            if ext not in JS_EXT and ext not in PRISMA_EXT:
                continue
            if fn.endswith(".min.js"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            try:
                _resolve_safe_path(rel)        # same sandbox guard as read_file
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
            except (ValueError, OSError):
                continue
            yield rel, ext


def _files() -> list:
    global _LIST_CACHE
    if _LIST_CACHE is None:
        _LIST_CACHE = list(_iter_files())
    return _LIST_CACHE


def _is_test_path(rel: str) -> bool:
    r = rel.lower().replace("\\", "/")
    return any(t in r for t in ("/test/", "/tests/", "__tests__", ".test.", ".spec."))


def find_definitions(name: str, path_hint: str = "", collect_refs: bool = True):
    """
    Shared by view_code_item and function_history.
    Returns (found, refs). found items: (test_penalty, kind_rank, rel, kind, start, end)
    sorted best-first; refs: [(rel, line_no, snippet)] call sites.
    """
    n = re.escape(name)
    ref_re = re.compile(rf"(?<![\w$]){n}\s*\(")
    hint = path_hint.lower().replace("\\", "/")
    found, refs = [], []
    for rel, ext in _files():
        if hint and hint not in rel.lower().replace("\\", "/"):
            continue
        lines = _lines(rel)
        patterns = _prisma_patterns(n) if ext in PRISMA_EXT else _js_patterns(n)
        i = 0
        while i < len(lines):
            line = lines[i]
            kind = None
            for k, pat in patterns:
                if pat.search(line) and not (k == "method" and "=>" in line):
                    kind = k
                    break
            if kind:
                end = _find_end(lines, i)
                found.append((1 if _is_test_path(rel) else 0, _KIND_RANK[kind], rel, kind, i, end))
                i = end + 1
                continue
            if collect_refs and ext in JS_EXT and len(refs) < 20 and ref_re.search(line):
                refs.append((rel, i + 1, line.strip()[:110]))
            i += 1
    found.sort(key=lambda t: (t[0], t[1], t[2], t[4]))
    return found, refs


def _render(name: str, item, max_lines: int):
    _, _, rel, kind, start, end = item
    lines = _lines(rel)
    total = end - start + 1
    shown_end = min(end, start + max_lines - 1)
    body = "\n".join(f"{k + 1}: {lines[k]}" for k in range(start, shown_end + 1))
    header = f"== {kind} {name} -- {rel.replace(os.sep, '/')}:{start + 1}-{end + 1} ({total} lines) =="
    if shown_end < end:
        body += f"\n... [{end - shown_end} more lines; use read_file with start_line={shown_end + 2}]"
    calls = _callees("\n".join(lines[start:end + 1]), name)
    return header + "\n" + body + ("\nCalls: " + ", ".join(calls) if calls else ""), calls


def _follow(root_name: str, callee_names: list[str], depth_limit: int) -> list[str]:
    """Inline definitions of project-defined callees, breadth-first, capped."""
    depth_limit = min(depth_limit, MAX_FOLLOW_DEPTH)
    seen = {root_name}
    out: list[str] = []
    frontier = [(c, root_name) for c in callee_names]
    depth = 1
    while frontier and depth <= depth_limit and len(out) < MAX_FOLLOWED:
        nxt = []
        for full, parent in frontier:
            cand = full.split(".")[-1]
            if cand in seen or cand in _COMMON_METHODS or len(cand) < 3 or not _NAME_RE.match(cand):
                continue
            seen.add(cand)
            found, _ = find_definitions(cand, collect_refs=False)
            if not found or len(found) > 2:     # library call, or too ambiguous to guess
                continue
            text, subcalls = _render(cand, found[0], FOLLOW_MAX_LINES)
            out.append(f"-- followed from {parent} (depth {depth}) --\n{text}")
            nxt.extend((c, cand) for c in subcalls)
            if len(out) >= MAX_FOLLOWED:
                break
        frontier = nxt
        depth += 1
    return out


@tool
def view_code_item(name: str, path_hint: str = "", max_lines: int = 120, follow: int = 1) -> str:
    """
    Find a function, class, method, arrow-function variable, or Prisma model
    by NAME anywhere in the target project and return its full implementation
    with line numbers, in ONE call. Also returns what it calls, where it is
    called from, and (follow=1 or 2) the implementations of the project
    functions it calls, e.g. controller -> service -> repository, so one call
    traces the whole path. Example names: 'getOrders', 'OrdersController',
    'Order' (the schema.prisma model, including its indexes). path_hint
    narrows to paths containing that text; max_lines caps each definition;
    follow=0 disables following, follow=2 goes two levels deep.
    """
    if not _NAME_RE.match(name or ""):
        return "ERROR: name must be a single identifier (letters, digits, _ or $), e.g. 'getOrders'."
    reset_caches()
    found, refs = find_definitions(name, path_hint)
    if not found:
        return (f"No definition of '{name}' found"
                f"{' under paths containing ' + repr(path_hint) if path_hint else ''}. "
                "It may come from a library, be built dynamically, or have a different name; "
                "try search_code with a partial name.")

    blocks, top_calls = [], []
    for idx, item in enumerate(found[:MAX_ITEMS]):
        text, calls = _render(name, item, max_lines)
        blocks.append(text)
        if idx == 0:
            top_calls = calls
    if len(found) > MAX_ITEMS:
        blocks.append(f"({len(found) - MAX_ITEMS} more definitions not shown; use path_hint to pick one)")

    def_locs = {(f[2], f[4] + 1) for f in found}
    callers = [r for r in refs if (r[0], r[1]) not in def_locs][:MAX_REFS]
    if callers:
        blocks.append("Called from:\n" + "\n".join(
            f"  {r[0].replace(os.sep, '/')}:{r[1]}: {r[2]}" for r in callers))

    followed = _follow(name, top_calls, follow) if follow > 0 else []
    result = "\n\n".join(blocks + followed)
    while len(result) > MAX_OUTPUT_CHARS and followed:
        followed.pop()                      # drop whole followed items, never cut code mid-line
        result = "\n\n".join(blocks + followed)
    if len(result) > MAX_OUTPUT_CHARS:
        result = result[:MAX_OUTPUT_CHARS] + "\n... [output truncated; lower max_lines or add path_hint]"
    return result
