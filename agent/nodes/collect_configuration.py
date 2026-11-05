from agent.state import InvestigationState
from .common import interrupt


def collect_configuration(_state: InvestigationState) -> dict:
    """
    Pause the graph and ask the user for the target project
    and whether live database inspection is required.
    """

    response = interrupt({
        "type": "collect_configuration",
        "message": "Configure the target project before starting the investigation.",
        "required_fields": {
            "project_root": "Absolute path to the target project directory",
            "inspect_database": "true or false"
        }
    })

    if not isinstance(response, dict):
        error = "Invalid configuration response."
        project_root = ""
        inspect_database = False
    else:
        project_root = response.get("project_root")
        inspect_database = response.get("inspect_database")
        error = ""

    if not error and (not isinstance(project_root, str) or not project_root.strip()):
        error = "A valid project root is required."
    elif not error and type(inspect_database) is not bool:
        error = "inspect_database must be true or false."

    if error:
        return {
            "project_root": "",
            "inspect_database": False,
            "project_validation_status": "invalid",
            "database_access_status": "not_requested",
            "configuration_confirmed": False,
            "db_setup_confirmed": False,
            "db_setup_attempts": 0,
            "initialization_status": "error",
            "initialization_error": error,
        }

    return {
        "project_root": project_root.strip(),
        "inspect_database": inspect_database,
        "project_validation_status": "pending",
        "database_access_status": (
            "pending" if inspect_database else "not_requested"
        ),
        "configuration_confirmed": False,
        "db_setup_confirmed": False,
        "db_setup_attempts": 0,
        "initialization_status": "pending",
        "initialization_error": "",
    }
