# Codebase Detective

Codebase Detective is an AI-assisted investigation tool for tracing a bug report through a real codebase and database, then producing an evidence-based root-cause analysis. It is designed for performance and logic investigations in Node.js/TypeScript applications, especially where the issue may involve routing, controller/service behavior, Prisma queries, Redis caching, or data access patterns.

The project runs a ReAct-style investigation loop using LangGraph. It gathers facts from the target codebase, inspects relevant files and database calls, and narrows the problem down to the most likely explanation with citations from the code.

## What it does

- Reads a natural-language bug report or security incident description
- Builds a concise case brief from the target project structure and schema
- Generates hypotheses about likely root causes
- Uses tools to inspect files, routes, services, and database queries
- Evaluates evidence as it accumulates instead of guessing
- Produces a final report with the most likely cause and supporting findings

## Typical use cases

- API latency investigations
- N+1 query or missing pagination checks
- Data exposure or authorization bugs
- Route/controller/service tracing
- Prisma or SQL query debugging

## Project structure

```text
.
├── agent/
│   ├── graph.py              # investigation workflow and routing
│   ├── nodes/                 # one module per graph node
│   │   ├── __init__.py        # compatibility exports for agent.nodes imports
│   │   ├── common.py          # shared settings, prompts, and output schemas
│   │   ├── helpers.py         # evidence, history, and notebook helpers
│   │   ├── can_conclude.py
│   │   ├── choose_action.py
│   │   ├── collect_configuration.py
│   │   ├── configuration_confirmation.py
│   │   ├── configuration_error.py
│   │   ├── evaluate_result.py
│   │   ├── generate_final_report.py
│   │   ├── generate_hypotheses.py
│   │   ├── prepare_case.py
│   │   ├── reject_conclusion.py
│   │   ├── request_db_setup.py
│   │   ├── run_tools.py
│   │   ├── validate_database.py
│   │   └── validate_project.py
│   ├── state.py               # state schema for the investigation
│   └── codebase_detective_tools/
│       ├── README.md         # tool descriptions and setup notes
│       ├── tools/
│       │   ├── code_item_tools.py
│       │   ├── code_search.py
│       │   ├── db_call_scan.py
│       │   ├── db_tools.py
│       │   ├── file_tools.py
│       │   ├── history_tools.py
│       │   ├── http_tools.py
│       │   ├── profile_tools.py
│       │   ├── route_tools.py
│       │   └── table_tools.py
├── case_brief.py             # builds the initial project brief
├── config.py                 # environment configuration
├── main.py                   # example investigation runner
├── run.py                    # CLI entry point for investigations
├── query.txt                 # sample investigation question
├── requirements.txt          # Python dependencies
├── tests/
│   ├── test_tools.py
│   └── test_wiring.py
├── .env                      # local environment variables
├── report.md                 # sample generated report
└── README.md                 # project documentation
```

## Requirements

- Python 3.10+
- Access to the target application codebase
- A read-only PostgreSQL database user for the target app
- A project root selected during initialization

## Setup

1. Create and activate a Python virtual environment if desired.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure target-specific settings through a per-investigation `DetectiveContext`, not `.env`. A host application can call `investigate` with a context assembled from its secure credential store:

```python
from agent.graph import investigate
from agent.runtime_context import DetectiveContext

context = DetectiveContext(
    database_url=secure_store.get("detective-database-url"),
    database_tables=("app.bookings", "app.users"),
    base_url="http://localhost:3000",
    auth_token=secure_store.get("target-api-token"),
    routes_manifest="routes.json",
)
state = investigate(question, runtime_context=context)
```

The initialization flow asks for the project root and whether database inspection is wanted. `database_url` is only needed when inspection is enabled, and that mode requires an explicit, unique `schema.table` allowlist. Passwords and tokens remain in runtime context and are never placed in graph state, prompts, logs, or checkpoints. Use a dedicated, non-privileged read-only database role.

## Running an investigation

The easiest way to start is through the CLI:

```bash
python run.py -f query.txt
```

You can also pass a question directly:

```bash
python run.py "GET /api/orders takes 4 seconds"
```

For more debugging output while the ReAct loop runs:

```bash
python run.py -f query.txt --debug
```

To save the report to a file:

```bash
python run.py -f query.txt --save report.md
```

## Example workflow

The project is designed around a single investigation flow:

1. A bug report or question is supplied.
2. The graph asks for the project root and whether database inspection is needed.
3. It validates the project directory and, if requested, database connectivity and read-only catalog access.
4. It asks for explicit confirmation before starting the investigation.
5. `case_brief.py` creates a compact factual summary; the graph then runs `prepare_case -> generate_hypotheses -> choose_action`.
6. The agent gathers evidence, evaluates it, and continues the investigation or generates a final report.

The initialization prompts use LangGraph `interrupt()` and must be answered by the caller before the workflow can continue. When resuming an interrupted investigation, supply the same secure runtime context again; LangGraph does not persist it in checkpoints.

## Understanding the environment and safety model

This project is built around a strict read-only investigation model:

- It should inspect the target project, not modify it.
- It is meant to query the database with a read-only user.
- It should use evidence from code and database behavior, not assumptions.
- Historical or runtime context can be checked with tool support when available.

## Notes

- `case_brief.py` is intentionally factual and should not contain conclusions or guessed root causes.
- `run.py` is the main entry point for one-off investigations.
- `main.py` shows a direct programmatic example of invoking the workflow.
- The repository includes test coverage for tool wiring and helper behavior in the `tests/` directory.

## Development notes

If you want to extend the project:

- add new investigation tools under `agent/codebase_detective_tools/tools/`
- register them in the tool set used by the investigation graph
- keep the tool outputs measured and factual, leaving final diagnosis to the agent’s reasoning

## License

This project does not currently include a license header or project-level license file. If you plan to distribute or reuse it, add a license that matches your intended usage.
