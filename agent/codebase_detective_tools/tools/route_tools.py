import difflib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from agent.runtime_context import DetectiveContext


def load_routes(routes_manifest: str | None, project_root: str | None) -> list[dict]:
    if not routes_manifest:
        raise FileNotFoundError("No routes manifest is configured for this investigation.")
    path = Path(routes_manifest)
    if not path.is_absolute():
        if not project_root:
            raise FileNotFoundError("No project root is configured for this investigation.")
        path = Path(project_root) / path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"Routes manifest not found at {path}. "
            "Generate it with scripts/dump_routes.js (see README)."
        )
    data = json.loads(path.read_text())
    return [{"path": r["path"], "methods": {m.upper() for m in r["methods"]}} for r in data]


def _to_regex(route_path: str) -> re.Pattern:
    # '/tickets/:id' -> '^/tickets/[^/]+/?$'
    body = re.sub(r":[^/]+", "[^/]+", route_path.rstrip("/") or "/")
    return re.compile(f"^{body}/?$")


def check_route(
    path: str,
    method: str = "GET",
    routes_manifest: str | None = None,
    project_root: str | None = None,
) -> tuple[bool, str]:
    """Return (is_valid, message). Ignores the query string when matching."""
    routes = load_routes(routes_manifest, project_root)
    clean = urlparse(path).path
    for r in routes:
        if _to_regex(r["path"]).match(clean):
            if method in r["methods"]:
                return True, r["path"]
            return False, (
                f"Route {r['path']} exists but does not accept {method}. "
                f"Allowed: {sorted(r['methods'])}"
            )
    get_paths = [r["path"] for r in routes if "GET" in r["methods"]]
    close = difflib.get_close_matches(clean, get_paths, n=3, cutoff=0.4)
    hint = f" Closest GET routes: {close}" if close else ""
    return False, f"No registered route matches '{clean}'.{hint} Call list_api_routes to see all routes."


@tool
def list_api_routes(runtime: ToolRuntime[DetectiveContext]) -> str:
    """
    List every API route the target backend actually registers, with its
    HTTP methods (e.g. 'GET /api/tickets', 'GET /api/tickets/:id'). Call
    this BEFORE time_endpoint so you use a real path instead of guessing.
    Path parameters like ':id' must be replaced with real values.
    """
    try:
        context = runtime.context
        routes = load_routes(
            context.routes_manifest if context else None,
            context.project_root if context else None,
        )
    except FileNotFoundError as e:
        return f"ERROR: {e}"
    lines = [f"{', '.join(sorted(r['methods']))} {r['path']}" for r in routes]
    return "\n".join(sorted(lines))[:4000]
