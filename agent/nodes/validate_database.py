from agent.state import InvestigationState
from agent.database_access import (
    DatabaseSetupRequired,
    VALIDATION_STATEMENT_TIMEOUT_MS,
    connect_read_only,
    parse_database_tables,
    validate_database_permissions,
)
from .common import DetectiveContext, Runtime, log, psycopg2



# ---------------------------------------------------------
# Database validation
# ---------------------------------------------------------

def validate_database(
    state: InvestigationState,
    runtime: Runtime[DetectiveContext]
) -> dict:
    """
    Check PostgreSQL connectivity, read-only transaction mode,
    and access to the index catalog.

    Credentials are supplied through runtime context,
    never through graph state.
    """

    if not state.get("inspect_database", False):
        return {
            "database_access_status": "not_requested",
            "initialization_error": ""
        }

    context = runtime.context
    database_url = context.database_url if context is not None else None
    database_tables = context.database_tables if context is not None else ()

    if not database_url:
        return {
            "database_access_status": "failed",
            "initialization_error": (
                "Database inspection was requested, but no database "
                "credentials were supplied."
            )
        }

    try:
        parse_database_tables(database_tables)
    except ValueError as exc:
        log.warning("Database validation configuration is incomplete: %s", exc)
        return {
            "database_access_status": "failed",
            "initialization_error": str(exc),
        }

    conn = None

    try:
        conn = connect_read_only(
            database_url,
            database_tables,
            statement_timeout_ms=VALIDATION_STATEMENT_TIMEOUT_MS,
        )

        with conn.cursor() as cursor:
            memberships = validate_database_permissions(cursor, database_tables)

        log.info(
            "Database validation successful; tables=%d, enhanced_stats=%s.",
            len(database_tables),
            "pg_monitor" in memberships,
        )

        return {
            "database_access_status": "ready",
            "initialization_error": ""
        }

    except (DatabaseSetupRequired, psycopg2.errors.InsufficientPrivilege) as exc:
        log.warning("Database role does not meet the access policy: %s", type(exc).__name__)

        return {
            "database_access_status": "needs_setup",
            "initialization_error": (
                str(exc) if isinstance(exc, DatabaseSetupRequired)
                else "The database login lacks required catalog permissions."
            )
        }

    except psycopg2.Error as exc:
        # Do not expose the connection URL or password in the error.
        log.warning(
            "Database validation failed: %s",
            type(exc).__name__
        )

        return {
            "database_access_status": "failed",
            "initialization_error": (
                "Database validation failed. Check the connection "
                "settings and database availability."
            )
        }

    finally:
        if conn is not None:
            conn.close()
