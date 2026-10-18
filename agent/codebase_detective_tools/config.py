"""
Central config for the Codebase Detective tools.

Everything here is read from environment variables (via .env) rather than
hardcoded, so the same tool code can point at TicketGuard, the Student
Portal, or any other target project just by changing .env.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Absolute path to the root of the project the agent is allowed to inspect.
# Every file tool checks paths against this and refuses anything outside it.
PROJECT_ROOT = os.environ["TARGET_PROJECT_ROOT"]

# Connection string for a READ-ONLY Postgres user. See README.md for why
# this must not be your app's normal (read-write) connection string.
DATABASE_URL = os.environ["TARGET_DATABASE_URL"]

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
