from agent.state import InvestigationState
from agent.runtime_context import DetectiveContext
from langgraph.runtime import Runtime
from .common import Path, os


def validate_project(
    state: InvestigationState,
    runtime: Runtime[DetectiveContext] | None = None,
) -> dict:
    """
    Validate that the supplied target project directory exists
    and can be read.
    """

    project_root = state.get("project_root")

    if not isinstance(project_root, str) or not project_root.strip():
        return {
            "project_validation_status": "invalid",
            "initialization_error": "Project root was not provided."
        }

    try:
        root = Path(project_root).expanduser().resolve()

        if not root.exists():
            raise ValueError("The specified project directory does not exist.")

        if not root.is_dir():
            raise ValueError("The specified project root is not a directory.")

        if not os.access(root, os.R_OK | os.X_OK):
            raise ValueError("The project directory is not readable.")
        with os.scandir(root) as entries:
            next(entries, None)

        validated_root = str(root)
        if runtime is not None and runtime.context is not None:
            runtime.context.project_root = validated_root

        return {
            "project_root": validated_root,
            "project_validation_status": "valid",
            "initialization_error": ""
        }

    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "project_validation_status": "invalid",
            "initialization_error": (
                str(exc) if isinstance(exc, ValueError)
                else "The project directory could not be read."
            )
        }
