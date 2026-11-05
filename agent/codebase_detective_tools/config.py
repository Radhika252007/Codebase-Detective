"""
Central config for the Codebase Detective tools.

Everything here is read from environment variables (via .env) rather than
hardcoded, so the same tool code can point at TicketGuard, the Student
Portal, or any other target project just by changing .env.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Legacy defaults remain for tools that have not yet moved to runtime context.
PROJECT_ROOT = os.environ.get("TARGET_PROJECT_ROOT", os.getcwd())

# Runtime database validation should receive its connection through
# DetectiveContext; keep this optional for legacy tool callers.
DATABASE_URL = os.environ.get("TARGET_DATABASE_URL")

# Base URL of the target app's running server, used by time_endpoint.
TARGET_BASE_URL = os.environ.get("TARGET_BASE_URL", "http://localhost:3000")

# Output caps, so no single tool result can flood the LLM's context.
MAX_FILE_CHARS = 4000
MAX_SEARCH_RESULTS = 30
MAX_EXPLAIN_CHARS = 4000

# Route manifest produced by scripts/dump_routes.js (path to routes.json).
ROUTES_MANIFEST = os.environ.get("TARGET_ROUTES_MANIFEST", "routes.json")

# Optional: bearer token if the target's endpoints require JWT auth.
AUTH_TOKEN = os.environ.get("TARGET_AUTH_TOKEN", "")
