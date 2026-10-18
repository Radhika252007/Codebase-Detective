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
)
from agent.state import InvestigationState

conn = sqlite3.connect(os.getenv("DETECTIVE_DB", "codebase.db"), check_same_thread=False)
checkpointer = SqliteSaver(conn)

graph = StateGraph(InvestigationState)

# Nodes
graph.add_node("prepare_case", prepare_case)
graph.add_node("generate_hypotheses", generate_hypotheses)
graph.add_node("choose_action", choose_action)
graph.add_node("run_tools", run_tools)
graph.add_node("evaluate_result", evaluate_result)
graph.add_node("reject_conclusion", reject_conclusion)
graph.add_node("final_report", generate_final_report)


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


# Edges
graph.add_edge(START, "prepare_case")
graph.add_edge("prepare_case", "generate_hypotheses")
graph.add_edge("generate_hypotheses", "choose_action")
graph.add_conditional_edges(
    "choose_action",
    route_after_choose_action,
    {"run_tools": "run_tools", "reject_conclusion": "reject_conclusion", "final_report": "final_report"},
)
graph.add_edge("run_tools", "evaluate_result")
graph.add_edge("evaluate_result", "choose_action")
graph.add_edge("reject_conclusion", "choose_action")
graph.add_edge("final_report", END)

workflow = graph.compile(checkpointer=checkpointer)


def investigate(bug_report: str, thread_id: str | None = None, recursion_limit: int = 60) -> dict:
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
    return workflow.invoke({"bug_report": bug_report}, config=config)