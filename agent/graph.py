"""
Graph wiring for the Codebase Detective.

  START -> prepare_case -> generate_hypotheses -> choose_action
  choose_action -> run_tools -> evaluate_result -> choose_action        (the ReAct loop)
  choose_action -> reject_conclusion -> choose_action                   (gate says: not done yet)
  choose_action -> final_report -> END                                  (confirmed, or forced stop)

Usage:
    from agent.graph import investigate
    state = investigate("Users report GET /orders takes about 4 seconds")
    print(state["final_report"])

Environment:
    DETECTIVE_DB     sqlite file for the checkpointer (default codebase.db; ":memory:" for tests)
    DETECTIVE_DEBUG  set to 1 to print messages, evaluations, notebook and hypotheses each round
"""
import os
import sqlite3
import uuid

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    MAX_CONCLUSION_REJECTIONS,
    can_conclude,
    choose_action,
    evaluate_result,
    generate_final_report,
    generate_hypotheses,
    prepare_case,
    reject_conclusion,
    run_tools,
    collect_configuration,
    validate_project,
    validate_database,
    request_db_setup,
    configuration_confirmation,
    configuration_error
)
from agent.state import InvestigationState
from agent.runtime_context import DetectiveContext

conn = sqlite3.connect(os.getenv("DETECTIVE_DB", "codebase.db"), check_same_thread=False)
checkpointer = SqliteSaver(conn)

graph = StateGraph(InvestigationState, context_schema=DetectiveContext)

# Nodes
graph.add_node("prepare_case", prepare_case)
graph.add_node("generate_hypotheses", generate_hypotheses)
graph.add_node("choose_action", choose_action)
graph.add_node("run_tools", run_tools)
graph.add_node("evaluate_result", evaluate_result)
graph.add_node("reject_conclusion", reject_conclusion)
graph.add_node("final_report", generate_final_report)
graph.add_node("collect_configuration", collect_configuration)
graph.add_node("validate_project", validate_project)
graph.add_node("validate_database", validate_database)
graph.add_node("request_db_setup", request_db_setup)
graph.add_node("configuration_confirmation", configuration_confirmation)
graph.add_node("configuration_error", configuration_error)


def route_after_project_validation(state: InvestigationState) -> str:
    """Decide whether database validation is required."""

    if state.get("project_validation_status") != "valid":
        return "configuration_error"

    if state.get("inspect_database", False):
        return "validate_database"

    return "configuration_confirmation"

def route_after_database_validation(state: InvestigationState) -> str:
    """Route based on database access status."""

    status = state.get("database_access_status")

    if status == "ready":
        return "configuration_confirmation"

    if status == "needs_setup" and state.get("db_setup_attempts", 0) == 0:
        return "request_db_setup"

    return "configuration_error"

def route_after_db_setup(state: InvestigationState) -> str:
    """Check whether the user completed database setup."""

    if not state.get("db_setup_confirmed", False):
        return "configuration_error"

    return "validate_database"

def route_after_configuration(state: InvestigationState) -> str:
    """Only start investigation after configuration approval."""

    if state.get("configuration_confirmed", False):
        return "prepare_case"

    return "configuration_error"

def route_after_choose_action(state: InvestigationState) -> str:
    """The LLM proposes; this function decides."""
    messages = state.get("messages", [])
    last = messages[-1] if messages else None

    if last is not None and getattr(last, "tool_calls", None):
        return "run_tools"                                   # the agent wants more evidence
    if state.get("stop_reason") == "step_limit":
        return "final_report"                                # forced stop
    allowed, _ = can_conclude(state)
    if allowed or state.get("conclusion_rejections", 0) >= MAX_CONCLUSION_REJECTIONS:
        return "final_report"                                # confirmed (or gate gave up pushing)
    return "reject_conclusion"                               # not confirmed yet: back to work


graph.add_edge(START, "collect_configuration")
graph.add_edge("collect_configuration", "validate_project")

graph.add_conditional_edges(
    "validate_project",
    route_after_project_validation,
    {
        "validate_database": "validate_database",
        "configuration_confirmation": "configuration_confirmation",
        "configuration_error": "configuration_error",
    },
)

graph.add_conditional_edges(
    "validate_database",
    route_after_database_validation,
    {
        "configuration_confirmation": "configuration_confirmation",
        "request_db_setup": "request_db_setup",
        "configuration_error": "configuration_error",
    },
)

graph.add_conditional_edges(
    "request_db_setup",
    route_after_db_setup,
    {
        "validate_database": "validate_database",
        "configuration_error": "configuration_error",
    },
)

graph.add_conditional_edges(
    "configuration_confirmation",
    route_after_configuration,
    {
        "prepare_case": "prepare_case",
        "configuration_error": "configuration_error",
    },
)

graph.add_edge("configuration_error", END)


# --------------------------------------------------
# Edges: Existing investigation
# --------------------------------------------------

graph.add_edge("prepare_case", "generate_hypotheses")
graph.add_edge("generate_hypotheses", "choose_action")

graph.add_conditional_edges(
    "choose_action",
    route_after_choose_action,
    {
        "run_tools": "run_tools",
        "reject_conclusion": "reject_conclusion",
        "final_report": "final_report",
    },
)

graph.add_edge("run_tools", "evaluate_result")
graph.add_edge("evaluate_result", "choose_action")
graph.add_edge("reject_conclusion", "choose_action")
graph.add_edge("final_report", END)


# --------------------------------------------------
# Compile
# --------------------------------------------------

workflow = graph.compile(checkpointer=checkpointer)



def investigate(
    bug_report: str,
    thread_id: str | None = None,
    recursion_limit: int = 60,
    *,
    runtime_context: DetectiveContext | None = None,
) -> dict:
    """
    Run one investigation and return the final state.

    A NEW thread_id per investigation is essential: the checkpointer replays old messages for a
    reused id. Each round costs 3 graph steps (choose_action, run_tools, evaluate_result), so 8 rounds
    plus setup and report need more than LangGraph's default recursion limit of 25.
    """
    config = {
        "recursion_limit": recursion_limit,
        "configurable": {"thread_id": thread_id or str(uuid.uuid4())},
    }
    return workflow.invoke(
        {"bug_report": bug_report},
        config=config,
        context=runtime_context if runtime_context is not None else DetectiveContext(),
    )