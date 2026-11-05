from agent.state import InvestigationState
from .common import interrupt


def request_db_setup(_state: InvestigationState) -> dict:
    """
    Pause the graph and ask the user to configure a read-only
    PostgreSQL user before database inspection continues.
    """

    if _state.get("database_access_status") != "needs_setup":
        return {
            "db_setup_confirmed": False,
            "initialization_error": (
                "Database setup was requested without a permission failure."
            ),
        }

    response = interrupt({
        "type": "database_setup_required",
        "message": (
            "Database access could not be validated. "
            "Create a dedicated read-only PostgreSQL user, "
            "then confirm when the setup is complete."
        ),
        "instructions": [
            "Create a dedicated PostgreSQL role for the detective.",
            "Grant USAGE only on the configured application schemas and SELECT only on the configured schema.table allowlist.",
            "Do not grant CREATE, write privileges, ownership, or membership in privileged roles.",
            "Do not use your application's owner, admin, or superuser credentials.",
            "pg_monitor is optional and only enables additional database statistics; pg_stat_statements is also optional.",
            "Run the SQL manually in your database administration tool.",
            "Configure the dedicated connection and database_tables allowlist in runtime context if they changed.",
            "Resume the investigation after completing the setup."
        ],
        "required_fields": {
            "confirmed": "true after the read-only role and permissions are configured",
        },
    })

    confirmed = response is True or (
        isinstance(response, dict) and response.get("confirmed") is True
    )

    if confirmed:
        return {
            "db_setup_confirmed": True,
            "db_setup_attempts": _state.get("db_setup_attempts", 0) + 1,
            "database_access_status": "pending",
            "initialization_error": ""
        }

    return {
        "db_setup_confirmed": False,
        "database_access_status": "needs_setup",
        "initialization_error": (
            "Database read-only setup was not confirmed."
        )
    }
