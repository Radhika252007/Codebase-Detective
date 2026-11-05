from agent.state import InvestigationState
from agent.runtime_context import DetectiveContext
from langgraph.runtime import Runtime
from .common import build_case_brief


def prepare_case(
    state: InvestigationState,
    runtime: Runtime[DetectiveContext],
) -> dict:
    context = runtime.context
    project_root = state.get("project_root")
    if context is None or not isinstance(project_root, str) or not project_root:
        raise RuntimeError("The validated project root or runtime context is missing.")
    context.project_root = project_root

    return {
        "case_brief": build_case_brief(
            project_root=project_root,
            database_url=context.database_url if state.get("inspect_database", False) else None,
            database_tables=context.database_tables if state.get("inspect_database", False) else (),
        ),
        "steps_taken": 0,
        "conclusion_rejections": 0,
        "gate_feedback": "",
        "stop_reason": "",
        "files_inspected": [],
        "items_inspected": [],
        "dead_ends": [],
        "evidence": [],
        "evaluation": "",
        "current_hypothesis": "",
        "root_cause": "",
        "final_report": "",
    }
