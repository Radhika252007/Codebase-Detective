import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2

from agent.codebase_detective_tools.tools import ALL_TOOLS
from agent.codebase_detective_tools.tools.file_tools import (
    list_project_files,
    read_file,
)
from agent.codebase_detective_tools.tools.http_tools import time_endpoint
from agent.codebase_detective_tools.tools.route_tools import list_api_routes
from agent.codebase_detective_tools.tools.profile_tools import _snapshot
from agent.runtime_context import DetectiveContext


class RuntimeToolTests(unittest.TestCase):
    def test_runtime_context_is_not_exposed_in_tool_call_schemas(self):
        tools = [*ALL_TOOLS, list_project_files, time_endpoint, list_api_routes]
        for tool in tools:
            with self.subTest(tool=tool.name):
                self.assertNotIn("runtime", tool.tool_call_schema.model_fields)

    def test_file_tools_use_the_selected_project_root(self):
        with tempfile.TemporaryDirectory() as project_root:
            Path(project_root, "source.txt").write_text("selected project", encoding="utf-8")
            runtime = SimpleNamespace(
                context=DetectiveContext(project_root=project_root),
            )

            content = read_file.func("source.txt", runtime)
            listing = list_project_files.func(runtime)

        self.assertIn("selected project", content)
        self.assertIn("source.txt", listing)

    def test_http_tool_uses_runtime_url_auth_and_routes_manifest(self):
        response = SimpleNamespace(status_code=200, content=b"ok", text="")
        with tempfile.TemporaryDirectory() as project_root:
            manifest = Path(project_root, "routes.json")
            manifest.write_text(
                json.dumps([{"path": "/api/orders", "methods": ["GET"]}]),
                encoding="utf-8",
            )
            runtime = SimpleNamespace(
                context=DetectiveContext(
                    project_root=project_root,
                    base_url="https://target.example",
                    auth_token="runtime-token",
                    routes_manifest="routes.json",
                )
            )

            with patch("agent.codebase_detective_tools.tools.http_tools.requests.get",
                       return_value=response) as get:
                result = time_endpoint.func("/api/orders", runtime)

        get.assert_called_once_with(
            "https://target.example/api/orders",
            headers={"Authorization": "Bearer runtime-token"},
            timeout=15,
        )
        self.assertIn("status 200", result)

    def test_optional_statistics_permission_failure_is_not_repeated(self):
        connection = SimpleNamespace(
            cursor=MagicMock(),
            rollback=MagicMock(),
            close=MagicMock(),
        )
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = ("public",)
        cursor.execute.side_effect = [
            None,
            None,
            psycopg2.errors.InsufficientPrivilege("permission denied"),
            None,
        ]
        runtime = SimpleNamespace(
            context=DetectiveContext(
                database_tables=("app.users",),
                database_capabilities={},
            )
        )

        with patch(
            "agent.codebase_detective_tools.tools.profile_tools._connect",
            return_value=connection,
        ):
            _, _, first_note = _snapshot(runtime)
            _, _, second_note = _snapshot(runtime)

        self.assertIn("unreadable", first_note)
        self.assertIn("unavailable", second_note)
        self.assertEqual(cursor.execute.call_count, 4)


if __name__ == "__main__":
    unittest.main()
