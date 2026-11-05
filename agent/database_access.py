"""Shared least-privilege checks and connection settings for PostgreSQL access."""
import re
from collections.abc import Sequence

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import quote_ident

CONNECT_TIMEOUT_SECONDS = 5
VALIDATION_STATEMENT_TIMEOUT_MS = 5000
TOOL_STATEMENT_TIMEOUT_MS = 15000
_IDENTIFIER = re.compile(r"^[^\x00-\x20.]{1,63}$")
_WRITE_TABLE_PRIVILEGES = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


class DatabaseSetupRequired(Exception):
    """The configured role does not meet the database access policy."""


def parse_database_tables(database_tables: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Validate and normalize the explicitly allowed schema.table names."""
    if not database_tables:
        raise ValueError("An explicit database_tables allowlist is required.")

    parsed = []
    for item in database_tables:
        if not isinstance(item, str) or item.count(".") != 1:
            raise ValueError("Database table allowlist entries must be schema.table.")
        schema_name, table_name = item.split(".", 1)
        if not _IDENTIFIER.fullmatch(schema_name) or not _IDENTIFIER.fullmatch(table_name):
            raise ValueError("Database table allowlist contains an invalid identifier.")
        parsed.append((schema_name, table_name))

    if len(set(parsed)) != len(parsed):
        raise ValueError("Database table allowlist entries must be unique.")
    return tuple(parsed)


def connect_read_only(
    database_url: str | None,
    database_tables: Sequence[str],
    *,
    statement_timeout_ms: int = TOOL_STATEMENT_TIMEOUT_MS,
):
    """Open a bounded read-only connection with an allowlisted search path."""
    if not database_url:
        raise RuntimeError("No runtime database URL is configured.")
    if statement_timeout_ms <= 0:
        raise ValueError("Statement timeout must be positive.")

    table_scope = parse_database_tables(database_tables)
    schemas = tuple(dict.fromkeys(schema for schema, _ in table_scope))
    conn = psycopg2.connect(
        database_url,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        options=(
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={statement_timeout_ms}"
        ),
    )
    try:
        conn.set_session(readonly=True)
        search_path = ", ".join(
            ["pg_catalog", *(quote_ident(schema, conn) for schema in schemas)]
        )
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('search_path', %s, false)",
                (search_path,),
            )
    except Exception:
        conn.close()
        raise
    return conn


def validate_database_permissions(cursor, database_tables: Sequence[str]) -> set[str]:
    """Require a dedicated, non-owner role with read-only access to the allowlist."""
    table_scope = set(parse_database_tables(database_tables))
    allowed_schemas = {schema for schema, _ in table_scope}

    cursor.execute("""
        SELECT current_setting('transaction_read_only'),
               r.rolcanlogin,
               r.rolsuper,
               r.rolcreatedb,
               r.rolcreaterole,
               r.rolreplication,
               r.rolbypassrls,
               r.oid
        FROM pg_roles r
        WHERE r.rolname = current_user
    """)
    role = cursor.fetchone()
    if not role or role[0] != "on":
        raise DatabaseSetupRequired("The database connection is not read-only.")
    if not role[1] or any(role[2:7]):
        raise DatabaseSetupRequired(
            "The database login must be a dedicated, non-privileged role."
        )
    role_oid = role[7]

    cursor.execute("""
        WITH RECURSIVE memberships(roleid) AS (
            SELECT roleid FROM pg_auth_members WHERE member = %s
            UNION
            SELECT m.roleid
            FROM pg_auth_members m
            JOIN memberships inherited ON inherited.roleid = m.member
        )
        SELECT r.rolname
        FROM memberships m
        JOIN pg_roles r ON r.oid = m.roleid
    """, (role_oid,))
    memberships = {row[0] for row in cursor.fetchall()}
    unexpected_memberships = memberships - {"pg_monitor"}
    if unexpected_memberships:
        raise DatabaseSetupRequired(
            "The database login inherits privileges from an unapproved role."
        )

    cursor.execute("""
        SELECT n.nspname,
               has_schema_privilege(current_user, n.oid, 'USAGE'),
               has_schema_privilege(current_user, n.oid, 'CREATE'),
               n.nspowner = %s
        FROM pg_namespace n
        WHERE n.nspname <> 'information_schema'
          AND left(n.nspname, 3) <> 'pg_'
    """, (role_oid,))
    schemas = cursor.fetchall()
    known_schemas = {row[0] for row in schemas}
    if allowed_schemas - known_schemas:
        raise DatabaseSetupRequired(
            "The database login cannot access every allowlisted application schema."
        )
    for schema_name, has_usage, has_create, owns_schema in schemas:
        if schema_name in allowed_schemas:
            if not has_usage or has_create or owns_schema:
                raise DatabaseSetupRequired(
                    "The database login needs USAGE, but not CREATE or ownership, "
                    "on each allowlisted application schema."
                )
        elif has_usage or has_create or owns_schema:
            raise DatabaseSetupRequired(
                "The database login has access to a schema outside its allowlist."
            )

    privilege_checks = [
        "has_table_privilege(current_user, c.oid, 'SELECT')",
        "has_any_column_privilege(current_user, c.oid, 'SELECT')",
    ]
    privilege_checks.extend(
        f"has_table_privilege(current_user, c.oid, '{privilege}')"
        for privilege in _WRITE_TABLE_PRIVILEGES
    )
    privilege_checks.extend(
        f"has_any_column_privilege(current_user, c.oid, '{privilege}')"
        for privilege in ("INSERT", "UPDATE", "REFERENCES")
    )
    cursor.execute(sql.SQL("""
        SELECT n.nspname,
               c.relname,
               c.relkind,
               c.relowner = %s,
               {}
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname <> 'information_schema'
          AND left(n.nspname, 3) <> 'pg_'
    """).format(sql.SQL(", ").join(map(sql.SQL, privilege_checks))), (role_oid,))
    relations = cursor.fetchall()

    visible_tables = set()
    for row in relations:
        schema_name, table_name, _, is_owner, *privileges = row
        relation = (schema_name, table_name)
        table_select, column_select = privileges[:2]
        has_write_access = any(privileges[2:])
        if relation in table_scope:
            visible_tables.add(relation)
            if is_owner or has_write_access:
                raise DatabaseSetupRequired(
                    "The database login must not own or modify allowlisted application tables."
                )
            if not table_select and not column_select:
                raise DatabaseSetupRequired(
                    "The database login needs SELECT access to every allowlisted application table."
                )
        elif table_select or column_select or has_write_access:
            raise DatabaseSetupRequired(
                "The database login can access an application table outside its allowlist."
            )

    if table_scope - visible_tables:
        raise DatabaseSetupRequired(
            "One or more allowlisted application tables do not exist."
        )

    return memberships
