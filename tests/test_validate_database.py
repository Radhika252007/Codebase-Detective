import unittest
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import langchain_google_genai
import psycopg2


class _DummyModel:
    def bind_tools(self, tools):
        self.tools = tools
        return self


with patch.object(
    langchain_google_genai,
    "ChatGoogleGenerativeAI",
    return_value=_DummyModel(),
):
    database_node = import_module("agent.nodes.validate_database")
    validate_database = database_node.validate_database
db_tools = import_module("agent.codebase_detective_tools.tools.db_tools")
from agent.runtime_context import DetectiveContext


class ValidateDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value

    def test_skips_connection_when_database_inspection_is_not_requested(self):
        with patch.object(database_node, "connect_read_only") as connect:
            result = validate_database(
                {"inspect_database": False},
                SimpleNamespace(context=DetectiveContext()),
            )

        self.assertEqual(result["database_access_status"], "not_requested")
        connect.assert_not_called()

    def test_missing_runtime_database_url_fails_without_connecting(self):
        with patch.object(database_node, "connect_read_only") as connect:
            result = validate_database(
                {"inspect_database": True},
                SimpleNamespace(context=DetectiveContext(
                    database_tables=("public.users",),
                )),
            )

        self.assertEqual(result["database_access_status"], "failed")
        self.assertNotIn("database_url", result)
        connect.assert_not_called()

    def test_missing_runtime_context_fails_without_connecting(self):
        with patch.object(database_node, "connect_read_only") as connect:
            result = validate_database(
                {"inspect_database": True},
                SimpleNamespace(context=None),
            )

        self.assertEqual(result["database_access_status"], "failed")
        connect.assert_not_called()

    def test_requires_explicit_table_allowlist(self):
        with patch.object(database_node, "connect_read_only") as connect:
            result = validate_database(
                {"inspect_database": True},
                SimpleNamespace(context=DetectiveContext(
                    database_url="postgresql://test-host/test-database",
                )),
            )

        self.assertEqual(result["database_access_status"], "failed")
        self.assertIn("database_tables allowlist", result["initialization_error"])
        connect.assert_not_called()

    def test_connects_read_only_and_checks_catalog_access(self):
        database_url = "postgresql://test-host/test-database"
        database_tables = ("app.users", "app.orders")

        with patch.object(database_node, "connect_read_only", return_value=self.connection) as connect, \
             patch.object(database_node, "validate_database_permissions", return_value={"pg_monitor"}) as validate:
            result = validate_database(
                {"inspect_database": True},
                SimpleNamespace(
                    context=DetectiveContext(
                        database_url=database_url,
                        database_tables=database_tables,
                    )
                ),
            )

        self.assertEqual(result["database_access_status"], "ready")
        self.assertEqual(result["initialization_error"], "")
        connect.assert_called_once_with(
            database_url,
            database_tables,
            statement_timeout_ms=database_node.VALIDATION_STATEMENT_TIMEOUT_MS,
        )
        validate.assert_called_once_with(self.cursor, database_tables)
        self.connection.close.assert_called_once()

    def test_permission_policy_failure_requests_database_setup(self):
        from agent.database_access import DatabaseSetupRequired
        with patch.object(database_node, "connect_read_only", return_value=self.connection), \
             patch.object(
                 database_node,
                 "validate_database_permissions",
                 side_effect=DatabaseSetupRequired("The role must be read-only."),
             ):
            result = validate_database(
                {"inspect_database": True},
                SimpleNamespace(
                    context=DetectiveContext(
                        database_url="postgresql://safe",
                        database_tables=("public.users",),
                    )
                ),
            )

        self.assertEqual(result["database_access_status"], "needs_setup")
        self.assertIn("read-only", result["initialization_error"])
        self.connection.close.assert_called_once()

    def test_permission_denial_requests_database_setup(self):
        with patch.object(database_node, "connect_read_only", return_value=self.connection), \
             patch.object(
                 database_node,
                 "validate_database_permissions",
                 side_effect=psycopg2.errors.InsufficientPrivilege("permission denied"),
             ):
            result = validate_database(
                {"inspect_database": True},
                SimpleNamespace(
                    context=DetectiveContext(
                        database_url="postgresql://safe",
                        database_tables=("public.users",),
                    )
                ),
            )

        self.assertEqual(result["database_access_status"], "needs_setup")
        self.connection.close.assert_called_once()

    def test_connection_errors_do_not_expose_url_in_state_or_logs(self):
        secret_url = "postgresql://test-host/test-database"
        with self.assertLogs("detective", level="WARNING") as captured:
            with patch.object(
                database_node,
                "connect_read_only",
                side_effect=psycopg2.OperationalError(secret_url),
            ):
                result = validate_database(
                    {"inspect_database": True},
                    SimpleNamespace(
                        context=DetectiveContext(
                            database_url=secret_url,
                            database_tables=("public.users",),
                        )
                    ),
                )

        self.assertEqual(result["database_access_status"], "failed")
        self.assertNotIn(secret_url, str(result))
        self.assertNotIn(secret_url, "\n".join(captured.output))

    def test_runtime_context_repr_redacts_database_url_and_auth_token(self):
        context = DetectiveContext(
            database_url="postgresql://test-host/test-database",
            auth_token="secret-token",
        )

        self.assertNotIn("test-host", repr(context))
        self.assertNotIn("secret-token", repr(context))

    def test_database_tools_do_not_fall_back_to_local_connection(self):
        with self.assertRaisesRegex(RuntimeError, "per-investigation database runtime"):
            db_tools._connect()


if __name__ == "__main__":
    unittest.main()
