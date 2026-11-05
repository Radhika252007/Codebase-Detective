import unittest
from unittest.mock import MagicMock, patch

import case_brief


class CaseBriefDatabaseTests(unittest.TestCase):
    def test_live_schema_queries_use_runtime_url_and_allowlisted_tables(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [
            [("app", "bookings", "id", "integer")],
            [],
            [],
            [("app", "bookings", 12)],
        ]
        table_allowlist = ("app.bookings",)

        with patch.object(case_brief, "connect_read_only", return_value=connection) as connect:
            result = case_brief._schema_from_db(
                "postgresql://runtime-host/database",
                table_allowlist,
            )

        connect.assert_called_once_with(
            "postgresql://runtime-host/database",
            table_allowlist,
        )
        self.assertIn("app.bookings (12 rows)", result)
        self.assertEqual(
            [call.args[1] for call in cursor.execute.call_args_list],
            [("app", "bookings")] * 4,
        )


if __name__ == "__main__":
    unittest.main()
