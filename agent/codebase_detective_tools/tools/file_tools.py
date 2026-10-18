from pathlib import Path
from langchain_core.tools import tool
from config import PROJECT_ROOT, MAX_FILE_CHARS


def _resolve_safe_path(relative_path: str) -> Path:
    """
    Resolve a path the agent gave us and make sure it can't escape
    PROJECT_ROOT (e.g. via '../../etc/passwd'). This is the single most
    important safety check in the whole tool set -- everything else here
    is about giving the LLM useful information; this is about not letting
    it read anything outside the sandbox.
    """
    root = Path(PROJECT_ROOT).resolve()
    candidate = (root / relative_path).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError(f"Path '{relative_path}' is outside the project root.")
    return candidate


@tool
def list_project_files(relative_path: str = ".") -> str:
    """
    List files and folders inside the target project, starting from
    relative_path (relative to the project root, e.g. '.' or 'src/routes').
    Use this FIRST to understand the project layout before reading specific
    files. Goes at most 3 folders deep and skips node_modules, .git, dist
    and build, since those are never useful to inspect.
    """
    try:
        root = _resolve_safe_path(relative_path)
    except ValueError as e:
        return f"ERROR: {e}"

    if not root.exists():
        return f"ERROR: path does not exist: {relative_path}"

    skip = {"node_modules", ".git", "dist", "build", ".next"}
    lines: list[str] = []

    def walk(path: Path, depth: int) -> None:
        if depth > 3:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return
        for entry in entries:
            if entry.name in skip:
                continue
            marker = "/" if entry.is_dir() else ""
            lines.append(f"{'  ' * depth}{entry.name}{marker}")
            if entry.is_dir():
                walk(entry, depth + 1)

    walk(root, 0)
    result = "\n".join(lines) if lines else "(empty)"
    return result[:MAX_FILE_CHARS]


@tool
def read_file(relative_path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """
    Read a file from the target project, given a path relative to the
    project root (e.g. 'src/routes/orders.js'). Optionally pass start_line
    and end_line (1-indexed, inclusive) to read only part of a large file --
    use list_project_files or search_code first to find the right file and
    rough location, then read just that slice instead of the whole file.
    """
    try:
        path = _resolve_safe_path(relative_path)
    except ValueError as e:
        return f"ERROR: {e}"

    if not path.exists() or not path.is_file():
        return f"ERROR: file does not exist: {relative_path}"

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR reading file: {e}"

    lines = text.splitlines()
    start = max(1, start_line)
    end = end_line or len(lines)
    snippet = lines[start - 1:end]

    numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(snippet, start=start))
    if len(numbered) > MAX_FILE_CHARS:
        numbered = numbered[:MAX_FILE_CHARS] + "\n... [truncated -- request a narrower line range]"

    return numbered or "(no lines in that range)"
