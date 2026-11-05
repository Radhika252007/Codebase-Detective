from agent.state import InvestigationState
from .common import DetectiveContext, Runtime, interrupt


def configuration_confirmation(
    state: InvestigationState,
    runtime: Runtime[DetectiveContext],
) -> dict:
    """Require explicit approval after project and database checks pass."""
    project_valid = state.get("project_validation_status") == "valid"
    inspect_database = state.get("inspect_database", False)
    database_ready = state.get("database_access_status") == "ready"
    database_skipped = state.get("database_access_status") == "not_requested"
    project_root = state.get("project_root")

    if not project_valid or not isinstance(project_root, str) or not project_root:
        return {
            "configuration_confirmed": False,
            "initialization_error": "The project directory has not passed validation.",
        }

    if type(inspect_database) is not bool:
        return {
            "configuration_confirmed": False,
            "initialization_error": "Database inspection status is incomplete.",
        }

    if inspect_database and not database_ready:
        return {
            "configuration_confirmed": False,
            "initialization_error": "Database access has not passed validation.",
        }

    context = runtime.context
    database_connection_configured = bool(
        context is not None and context.database_url
    )
    if inspect_database and not database_connection_configured:
        return {
            "configuration_confirmed": False,
            "initialization_error": "Database connection settings are unavailable.",
        }

    if inspect_database and not context.database_tables:
        return {
            "configuration_confirmed": False,
            "initialization_error": (
                "Database inspection requires an explicit schema.table allowlist "
                "in runtime context."
            ),
        }

    if not inspect_database and not database_skipped:
        return {
            "configuration_confirmed": False,
            "initialization_error": "Database inspection status is incomplete.",
        }

    response = interrupt({
        "type": "configuration_confirmation",
        "message": "Review the validated investigation configuration.",
        "configuration": {
            "project_root": project_root,
            "inspect_database": inspect_database,
            "database_access_status": state["database_access_status"],
            "database_connection_configured": database_connection_configured,
            "database_tables_configured": len(context.database_tables) if context else 0,
        },
        "required_fields": {
            "confirmed": "true to start the investigation, false to cancel",
        },
    })

    confirmed = response is True or (
        isinstance(response, dict) and response.get("confirmed") is True
    )
    if confirmed:
        return {
            "configuration_confirmed": True,
            "initialization_status": "confirmed",
            "initialization_error": "",
        }

    return {
        "configuration_confirmed": False,
        "initialization_error": "Investigation configuration was not confirmed.",
    }
