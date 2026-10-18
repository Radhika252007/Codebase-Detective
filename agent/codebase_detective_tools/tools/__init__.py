from .file_tools import list_project_files, read_file
from .code_search import search_code
from .db_tools import run_explain, explain_query, get_indexes
from .http_tools import time_endpoint
from .route_tools import list_api_routes
from .code_item_tools import view_code_item
from .db_call_scan import scan_db_calls
from .profile_tools import profile_endpoint
from .table_tools import inspect_table, db_activity
from .history_tools import function_history

# The set handed to the agent (LangGraph ToolNode / bind_tools). Each tool answers a
# whole question in one call. Retired but still importable: list_project_files
# (the case brief covers layout), run_explain -> explain_query, get_indexes ->
# inspect_table, time_endpoint -> profile_endpoint, list_api_routes (needed a
# manifest script).
ALL_TOOLS = [
    view_code_item,
    scan_db_calls,
    profile_endpoint,
    explain_query,
    inspect_table,
    function_history,
    db_activity,
    read_file,
    search_code,
]
