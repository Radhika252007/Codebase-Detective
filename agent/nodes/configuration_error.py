from agent.state import InvestigationState


def configuration_error(state: InvestigationState) -> dict:
    """Return a terminal, user-visible initialization failure without secrets."""
    return {
        "configuration_confirmed": False,
        "initialization_status": "error",
        "initialization_error": (
            state.get("initialization_error")
            or "Investigation configuration is invalid or incomplete."
        ),
    }
