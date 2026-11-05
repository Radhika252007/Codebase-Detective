from agent.state import InvestigationState
from . import common
from .common import FINAL_REPORT_FORMAT, build_final_report_prompt
from .helpers import _text


def generate_final_report(state: InvestigationState) -> dict:
    hyps = state.get("hypotheses", [])
    confirmed = [h for h in hyps if h["status"] == "confirmed"]

    if confirmed:
        reason, status = "concluded", "CONFIRMED: " + "; ".join(h["id"] for h in confirmed)
    elif state.get("stop_reason") == "step_limit":
        reason, status = "step_limit", "NOT CONFIRMED: the step limit was reached before any hypothesis was confirmed."
    else:
        reason, status = "unconfirmed", "NOT CONFIRMED: the agent stopped without a confirmed hypothesis."

    prompt = build_final_report_prompt(
        state["bug_report"], hyps, state.get("evidence", []), state.get("evaluation", ""),
    ) + FINAL_REPORT_FORMAT.format(status=status)
    response = common.llm.invoke(prompt)

    return {
        "final_report": _text(response.content),
        "root_cause": "; ".join(f"{h['id']}: {h['description']}" for h in confirmed),
        "stop_reason": reason,
    }
