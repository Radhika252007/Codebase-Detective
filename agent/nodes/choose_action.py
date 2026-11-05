from agent.state import InvestigationState
from . import common
from .common import (
    ACCESS_PLAYBOOK,
    AGENT_RULES,
    MAX_STEPS,
    INVESTIGATION_PLAYBOOK,
    AIMessage,
    HumanMessage,
    SystemMessage,
    build_follow_up_prompt,
    build_initial_investigation_prompt,
    build_system_prompt,
)
from .helpers import _compress_history, _debug


def choose_action(state: InvestigationState) -> dict:
    messages = state.get("messages", [])
    steps_taken = state.get("steps_taken", 0)
    files_inspected = state.get("files_inspected", [])
    items_inspected = state.get("items_inspected", [])
    dead_ends = state.get("dead_ends", [])

    if steps_taken >= MAX_STEPS:
        return {
            "messages": [AIMessage(content="Investigation step limit reached. "
                                           "Prepare the final report using the evidence collected.")],
            "stop_reason": "step_limit",
        }

    system_message = SystemMessage(
        content=f"{build_system_prompt()}\n\n{AGENT_RULES}\n{INVESTIGATION_PLAYBOOK}\n{ACCESS_PLAYBOOK}"
    )

    if not messages:   # first investigation action
        human_message = HumanMessage(
            content=build_initial_investigation_prompt(
                bug_report=state["bug_report"],
                hypotheses=state["hypotheses"],
                case_brief=state["case_brief"],
                files_inspected=files_inspected,
                items_inspected=items_inspected,
                dead_ends=dead_ends,
            )
        )
        response = common.llm_with_tools.invoke([system_message, human_message])
        return {"messages": [human_message, response], "steps_taken": steps_taken + 1}

    context = build_follow_up_prompt(
        evaluation=state.get("evaluation", ""),
        hypotheses=state.get("hypotheses", []),
        evidence=state.get("evidence", []),
        files_inspected=files_inspected,
        items_inspected=items_inspected,
        dead_ends=dead_ends,
    )
    context += f"\n\nSteps used: {steps_taken}/{MAX_STEPS}."
    if steps_taken >= MAX_STEPS - 2:
        context += " Few steps remain: prefer the single most decisive check."
    feedback = state.get("gate_feedback", "")
    if feedback:   # shown once, as part of this turn only (not stored in the message history)
        context += f"\n\nNOTE FROM THE INVESTIGATION CONTROLLER: {feedback}"

    visible = _compress_history(messages, state.get("evidence", []))
    _debug("MESSAGES SENT TO THE MODEL", *[
        f"{m.type}: tool_calls={getattr(m, 'tool_calls', [])} {str(m.content)[:120]}" for m in visible
    ])
    response = common.llm_with_tools.invoke([system_message, *visible, HumanMessage(content=context)])
    return {"messages": [response], "steps_taken": steps_taken + 1, "gate_feedback": ""}
