"""State for the Codebase Detective investigation graph."""
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class Hypothesis(TypedDict):
    id: str                          # "H1", "H2", ...
    description: str
    status: str                      # untested | supported | confirmed | rejected
    supporting_evidence: list[str]   # "E3: one-sentence reason"
    contradicting_evidence: list[str]


class EvidenceEntry(TypedDict):
    id: str                          # "E1", "E2", ...
    tool: str
    tool_call_id: str                # pairs this entry with its ToolMessage
    input: dict
    output_summary: str              # first ~1000 chars of the raw tool output
    evaluation: str                  # one-line verdict, shown to the model later
    effects: list[dict]              # [{"hypothesis_id", "effect", "reason"}]
    explains_full_symptom: bool


class InvestigationState(TypedDict):
    # input
    bug_report: str

    # set once by prepare_case (no LLM)
    case_brief: str

    # investigation record
    hypotheses: list[Hypothesis]
    current_hypothesis: str
    evidence: list[EvidenceEntry]
    evaluation: str                  # verdict of the latest round
    files_inspected: list[str]       # detective's notebook
    items_inspected: list[str]
    dead_ends: list[str]

    # control
    steps_taken: int
    conclusion_rejections: int       # times the gate refused to let the agent finish
    gate_feedback: str               # one-shot message from the gate to the next choose_action
    stop_reason: str                 # "" | "step_limit" | "concluded" | "unconfirmed"

    # output
    root_cause: str
    final_report: str

    messages: Annotated[list[AnyMessage], add_messages]