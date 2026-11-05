import time
import requests
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from agent.runtime_context import DetectiveContext

from agent.codebase_detective_tools.tools.route_tools import check_route

_ALLOWED_METHODS = {"GET"}


@tool
def time_endpoint(
    path: str,
    runtime: ToolRuntime[DetectiveContext],
    method: str = "GET",
) -> str:
    """
    Measure how long a REAL endpoint on the target app takes to respond, in
    milliseconds. The path (e.g. '/api/tickets?userId=1') is validated
    against the app's registered routes first; if it doesn't exist you get
    the closest real routes back instead of a 404. Use list_api_routes first
    to find valid paths. Only GET is allowed, since investigating must never
    change data.
    """
    method = method.upper()
    if method not in _ALLOWED_METHODS:
        return f"ERROR: only {_ALLOWED_METHODS} requests are allowed."

    context = runtime.context
    if context is None or not context.base_url:
        return "ERROR: no per-investigation base URL is configured."
    if not path.startswith("/"):
        path = "/" + path

    try:
        ok, info = check_route(
            path,
            method,
            context.routes_manifest,
            context.project_root,
        )
    except FileNotFoundError as e:
        return f"ERROR: {e}"
    if not ok:
        return f"ERROR (not sent): {info}"

    headers = {"Authorization": f"Bearer {context.auth_token}"} if context.auth_token else {}
    url = context.base_url.rstrip("/") + path
    start = time.monotonic()
    try:
        response = requests.get(url, headers=headers, timeout=15)
    except Exception as e:
        return f"ERROR calling endpoint: {e}"
    elapsed_ms = (time.monotonic() - start) * 1000

    result = (
        f"GET {path} (route {info}) -> status {response.status_code}, "
        f"{elapsed_ms:.0f} ms, {len(response.content)} bytes"
    )
    if response.status_code >= 400:
        # Body tells the agent WHY: 'Cannot GET ...' vs an app-level JSON error.
        result += f"\nResponse body: {response.text[:200]}"
    return result
