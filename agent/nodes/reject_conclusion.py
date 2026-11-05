from agent.state import InvestigationState
from .can_conclude import can_conclude


def reject_conclusion(state: InvestigationState) -> dict:
    _, reason = can_conclude(state)
    return {
        "gate_feedback": f"You tried to finish, but the investigation is not complete. {reason}",
        "conclusion_rejections": state.get("conclusion_rejections", 0) + 1,
    }
