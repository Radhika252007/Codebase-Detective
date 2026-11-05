from agent.state import InvestigationState
from .common import MIN_TOOLS_TO_CONFIRM


def can_conclude(state: InvestigationState) -> tuple[bool, str]:
    hyps = state.get("hypotheses", [])
    if any(h["status"] == "confirmed" for h in hyps):
        return True, ""
    supported = [h["id"] for h in hyps if h["status"] == "supported"]
    if supported:
        return False, (
            f"{', '.join(supported)} look plausible but are not confirmed. Confirmation needs evidence from at "
            f"least {MIN_TOOLS_TO_CONFIRM} different tools, and the finding must account for the full size of "
            "the reported problem. Get an independent second source for it."
        )
    return False, "No hypothesis has supporting tool evidence yet. Test the most likely hypothesis with a tool."
