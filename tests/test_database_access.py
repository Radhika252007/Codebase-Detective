import unittest
from unittest.mock import MagicMock, patch

import psycopg2

from agent.database_access import (
    CONNECT_TIMEOUT_SECONDS,
    VALIDATION_STATEMENT_TIMEOUT_MS,
    connect_read_only,
    parse_database_tables,
    validate_database_permissions,
)


class DatabaseAccessTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value

    def test_table_scope_requires_unique_schema_qualified_identifiers(self):
        self.assertEqual(
            parse_database_tables(("app.users", "app.orders")),
            (("app", "users"), ("app", "orders")),
        )
        for invalid in ((), ("users",), ("app.users.extra",), ("app.",), ("app.users", "app.users")):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_database_tables(invalid)

    def test_connection_is_bounded_read_only_and_uses_allowlisted_search_path(self):
        with patch(
            "agent.database_access.psycopg2.connect",
            return_value=self.connection,
        ) as connect, patch(
            "agent.database_access.quote_ident",
            side_effect=lambda schema, _connection: f'"{schema}"',
        ):
            result = connect_read_only(
                "postgresql://test-host/test-database",
                ("app.users", "reporting.orders"),
                statement_timeout_ms=VALIDATION_STATEMENT_TIMEOUT_MS,
            )

        self.assertIs(result, self.connection)
        connect.assert_called_once_with(
            "postgresql://test-host/test-database",
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            options=(
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={VALIDATION_STATEMENT_TIMEOUT_MS}"
            ),
        )
        self.connection.set_session.assert_called_once_with(readonly=True)
        self.cursor.execute.assert_called_once_with(
            "SELECT set_config('search_path', %s, false)",
            ('pg_catalog, "app", "reporting"',),
        )

    def test_connect_requires_runtime_credentials_and_scope(self):
        with patch("agent.database_access.psycopg2.connect") as connect:
            for url, scope in (
                (None, ("app.users",)),
                ("postgresql://test-host/db", ()),
                ("postgresql://test-host/db", ("users",)),
            ):
                with self.subTest(url=url, scope=scope):
                    with self.assertRaises((RuntimeError, ValueError)):
                        connect_read_only(url, scope)
        connect.assert_not_called()

    def _valid_permission_cursor(self):
        self.cursor.fetchone.return_value = (
            "on",
            True,
            False,
            False,
            False,
            False,
            False,
            42,
        )
        self.cursor.fetchall.side_effect = [
            [("pg_monitor",)],
            [("app", True, False, False)],
            [("app", "users", "r", False, True, True, *([False] * 9))],
        ]
        return self.cursor

    def test_accepts_standalone_read_only_role_with_scoped_table_access(self):
        memberships = validate_database_permissions(
            self._valid_permission_cursor(),
            ("app.users",),
        )
        self.assertEqual(memberships, {"pg_monitor"})

    def test_rejects_superuser_or_other_privileged_login(self):
        role = ("on", True, True, False, False, False, False, 42)
        self.cursor.fetchone.return_value = role
        with self.assertRaisesRegex(Exception, "dedicated"):
            validate_database_permissions(self.cursor, ("app.users",))

    def test_rejects_access_outside_the_allowlisted_tables(self):
        cursor = self._valid_permission_cursor()
        cursor.fetchall.side_effect = [
            [],
            [("app", True, False, False)],
            [
                ("app", "users", "r", False, True, True, *([False] * 9)),
                ("app", "secrets", "r", False, True, True, *([False] * 9)),
            ],
        ]
        with self.assertRaisesRegex(Exception, "outside its allowlist"):
            validate_database_permissions(cursor, ("app.users",))

    def test_rejects_write_privileges_on_allowlisted_table(self):
        cursor = self._valid_permission_cursor()
        cursor.fetchall.side_effect = [
            [],
            [("app", True, False, False)],
            [("app", "users", "r", False, True, True, True, *([False] * 8))],
        ]
        with self.assertRaisesRegex(Exception, "must not own or modify"):
            validate_database_permissions(cursor, ("app.users",))

    def test_rejects_non_read_only_transaction(self):
        cursor = self._valid_permission_cursor()
        cursor.fetchone.return_value = ("off", True, False, False, False, False, False, 42)
        with self.assertRaisesRegex(Exception, "not read-only"):
            validate_database_permissions(cursor, ("app.users",))


if __name__ == "__main__":
    unittest.main()
