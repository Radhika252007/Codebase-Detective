import os
import tempfile
import unittest
import uuid
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DETECTIVE_DB"] = ":memory:"

import langchain_google_genai
from langgraph.types import Command


class _DummyModel:
    def bind_tools(self, tools):
        self.tools = tools
        return self


with patch.object(
    langchain_google_genai,
    "ChatGoogleGenerativeAI",
    return_value=_DummyModel(),
):
    from agent import nodes
    from agent.runtime_context import DetectiveContext

    collect_node = import_module("agent.nodes.collect_configuration")
    project_node = import_module("agent.nodes.validate_project")
    db_setup_node = import_module("agent.nodes.request_db_setup")
    confirmation_node = import_module(
        "agent.nodes.configuration_confirmation"
    )
    error_node = import_module("agent.nodes.configuration_error")
    graph_module = import_module("agent.graph")


class InitializationNodeTests(unittest.TestCase):
    def test_graph_exports_and_registers_all_initialization_nodes(self):
        node_names = graph_module.workflow.get_graph().nodes
        expected = {
            "collect_configuration",
            "validate_project",
            "validate_database",
            "request_db_setup",
            "configuration_confirmation",
            "configuration_error",
        }

        self.assertTrue(all(hasattr(nodes, name) for name in expected))
        self.assertTrue(expected.issubset(node_names))

    def test_graph_pauses_and_resumes_configuration_interrupts(self):
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        with tempfile.TemporaryDirectory() as project_root:
            first = graph_module.workflow.invoke(
                {"bug_report": "test initialization"},
                config=config,
            )
            self.assertIn("__interrupt__", first)
            self.assertEqual(
                first["__interrupt__"][0].value["type"],
                "collect_configuration",
            )

            second = graph_module.workflow.invoke(
                Command(resume={
                    "project_root": project_root,
                    "inspect_database": False,
                }),
                config=config,
            )
            self.assertIn("__interrupt__", second)
            self.assertEqual(
                second["__interrupt__"][0].value["type"],
                "configuration_confirmation",
            )

            final = graph_module.workflow.invoke(
                Command(resume=False),
                config=config,
            )

        self.assertEqual(final["initialization_status"], "error")
        self.assertEqual(
            final["initialization_error"],
            "Investigation configuration was not confirmed.",
        )

    def test_collect_configuration_requires_path_and_boolean(self):
        with patch.object(
            collect_node,
            "interrupt",
            return_value={
                "project_root": "  C:\\project  ",
                "inspect_database": False,
            },
        ):
            result = collect_node.collect_configuration({})

        self.assertEqual(result["project_root"], "C:\\project")
        self.assertFalse(result["inspect_database"])
        self.assertEqual(result["project_validation_status"], "pending")
        self.assertEqual(result["initialization_status"], "pending")

        with patch.object(
            collect_node,
            "interrupt",
            return_value={
                "project_root": "C:\\project",
                "inspect_database": "false",
            },
        ):
            invalid = collect_node.collect_configuration({})

        self.assertEqual(invalid["project_validation_status"], "invalid")
        self.assertEqual(invalid["initialization_status"], "error")
        self.assertFalse(invalid["inspect_database"])
        self.assertEqual(
            invalid["initialization_error"],
            "inspect_database must be true or false.",
        )

    def test_validate_project_checks_directory_and_returns_canonical_path(self):
        with tempfile.TemporaryDirectory() as directory:
            result = project_node.validate_project({"project_root": directory})
            self.assertEqual(result["project_validation_status"], "valid")
            self.assertEqual(result["project_root"], str(Path(directory).resolve()))

        invalid = project_node.validate_project(
            {"project_root": "C:\\path\\that\\does\\not\\exist"}
        )
        self.assertEqual(invalid["project_validation_status"], "invalid")
        self.assertEqual(
            invalid["initialization_error"],
            "The specified project directory does not exist.",
        )

    def test_request_db_setup_only_continues_on_explicit_confirmation(self):
        state = {"database_access_status": "needs_setup"}
        with patch.object(db_setup_node, "interrupt", return_value={"confirmed": True}):
            confirmed = db_setup_node.request_db_setup(state)
        self.assertTrue(confirmed["db_setup_confirmed"])
        self.assertEqual(confirmed["db_setup_attempts"], 1)
        self.assertEqual(confirmed["database_access_status"], "pending")

        with patch.object(db_setup_node, "interrupt", return_value={"confirmed": "true"}):
            declined = db_setup_node.request_db_setup(state)
        self.assertFalse(declined["db_setup_confirmed"])
        self.assertEqual(declined["database_access_status"], "needs_setup")

        with patch.object(db_setup_node, "interrupt") as interrupt:
            unexpected = db_setup_node.request_db_setup(
                {"database_access_status": "ready"}
            )
        self.assertFalse(unexpected["db_setup_confirmed"])
        interrupt.assert_not_called()

    def test_database_setup_prompt_is_not_repeated_after_retry_fails(self):
        self.assertEqual(
            graph_module.route_after_database_validation(
                {
                    "database_access_status": "needs_setup",
                    "db_setup_attempts": 0,
                }
            ),
            "request_db_setup",
        )
        self.assertEqual(
            graph_module.route_after_database_validation(
                {
                    "database_access_status": "needs_setup",
                    "db_setup_attempts": 1,
                }
            ),
            "configuration_error",
        )

    def test_confirmation_requires_successful_validation_and_hides_credentials(self):
        secret_url = "postgresql://secret-host/private"
        auth_token = "secret-token"
        state = {
            "project_root": "C:\\project",
            "project_validation_status": "valid",
            "inspect_database": True,
            "database_access_status": "ready",
        }
        runtime = SimpleNamespace(
            context=DetectiveContext(
                database_url=secret_url,
                auth_token=auth_token,
                database_tables=("app.users",),
            )
        )
        payloads = []

        def capture(payload):
            payloads.append(payload)
            return {"confirmed": True}

        with patch.object(confirmation_node, "interrupt", side_effect=capture):
            result = confirmation_node.configuration_confirmation(state, runtime)

        self.assertTrue(result["configuration_confirmed"])
        self.assertEqual(result["initialization_status"], "confirmed")
        prompt = repr(payloads[0])
        self.assertNotIn(secret_url, prompt)
        self.assertNotIn(auth_token, prompt)
        self.assertTrue(
            payloads[0]["configuration"]["database_connection_configured"]
        )

        invalid_state = dict(state, database_access_status="failed")
        with patch.object(confirmation_node, "interrupt") as interrupt:
            invalid = confirmation_node.configuration_confirmation(
                invalid_state,
                runtime,
            )
        self.assertFalse(invalid["configuration_confirmed"])
        interrupt.assert_not_called()

    def test_configuration_error_returns_terminal_user_visible_status(self):
        result = error_node.configuration_error(
            {"initialization_error": "The project root is invalid."}
        )
        self.assertFalse(result["configuration_confirmed"])
        self.assertEqual(result["initialization_status"], "error")
        self.assertEqual(
            result["initialization_error"],
            "The project root is invalid.",
        )


if __name__ == "__main__":
    unittest.main()
