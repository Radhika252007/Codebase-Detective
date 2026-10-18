import os
import re
import subprocess
from pathlib import Path

from langchain_core.tools import tool

from config import PROJECT_ROOT
from .code_item_tools import _NAME_RE, find_definitions, reset_caches

MAX_OUTPUT_CHARS = 6000
MAX_DIFF_LINES_PER_COMMIT = 25


def _git(args: list[str], timeout: int = 20):
    r = subprocess.run(["git", "-C", str(Path(PROJECT_ROOT).resolve())] + args,
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr.strip()


@tool
def function_history(name: str, path_hint: str = "", max_commits: int = 5) -> str:
    """
    Show how a function/class/method has changed over time: finds its current
    line range, then returns the commits that touched exactly those lines
    (hash, date, author, message, lines added/removed and the changed lines),
    newest first. Use it for 'it used to be fast' questions: what changed in
    this code recently. Also reports uncommitted changes. Read-only git.
    """
    if not _NAME_RE.match(name or ""):
        return "ERROR: name must be a single identifier, e.g. 'getOrders'."
    max_commits = max(1, min(int(max_commits), 10))
    reset_caches()
    found, _ = find_definitions(name, path_hint, collect_refs=False)
    if not found:
        return f"No definition of '{name}' found, so there is no line range to trace."
    _, _, rel, kind, start, end = found[0]
    relp = rel.replace(os.sep, "/")

    try:
        code, _, err = _git(["rev-parse", "--is-inside-work-tree"])
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"ERROR: git is not usable here ({type(e).__name__})."
    if code != 0:
        return f"ERROR: the project is not inside a git repository ({err[:100]})."

    status = _git(["status", "--porcelain", "--", relp])[1].strip()
    code, out, err = _git(["log", f"-L{start + 1},{end + 1}:{relp}", "-n", str(max_commits),
                           "--no-color", "--date=short", "--pretty=format:COMMIT|%h|%ad|%an|%s"], timeout=30)
    if code != 0:
        return f"ERROR from git log -L: {err[:200]}"

    commits, cur = [], None
    for line in out.splitlines():
        if line.startswith("COMMIT|"):
            _, h, d, a, subj = (line.split("|", 4) + [""] * 5)[:5]
            cur = {"h": h, "d": d, "a": a, "s": subj, "diff": []}
            commits.append(cur)
        elif cur is not None and line[:1] in "+-" and not line.startswith(("+++", "---")):
            cur["diff"].append(line)

    head = f"{kind} {name} -- {relp}:{start + 1}-{end + 1}"
    if not commits:
        text = head + "\nNo commits found that touched these lines (new/untracked file, or no history)."
    else:
        blocks = []
        for c in commits:
            plus = sum(1 for l in c["diff"] if l.startswith("+"))
            minus = len(c["diff"]) - plus
            shown = c["diff"][:MAX_DIFF_LINES_PER_COMMIT]
            more = len(c["diff"]) - len(shown)
            blocks.append(f"{c['h']} {c['d']} {c['a']}: {c['s']}  (+{plus} -{minus})\n"
                          + "\n".join("  " + l[:140] for l in shown)
                          + (f"\n  ... {more} more changed lines" if more else ""))
        text = f"{head} -- {len(commits)} commit(s) touched these lines, newest first\n" + "\n".join(blocks)
    if status:
        text += f"\nUncommitted changes in this file: {status}"
    return text[:MAX_OUTPUT_CHARS]
