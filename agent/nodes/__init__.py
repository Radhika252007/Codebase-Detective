"""Graph nodes, organized as one module per node."""

from . import common, run_tools as _run_tools_module
from .common import (
    ACCESS_PLAYBOOK,
    AGENT_RULES,
    DEBUG,
    DUPLICATE_PREFIX,
    EVALUATION_FORMAT,
    FINAL_REPORT_FORMAT,
    INVESTIGATION_PLAYBOOK,
    MAX_CONCLUSION_REJECTIONS,
    MAX_HYPOTHESES,
    MAX_STEPS,
    MIN_TOOLS_TO_CONFIRM,
    OLD_RESULT_CHARS,
    REPEATABLE_TOOLS,
    SUMMARY_CHARS,
    Evaluation,
    GeneratedHypothesis,
    GeneratedHypothesisList,
    HypothesisEffect,
)
from .helpers import (
    _HEADER_RE,
    _DEAD_END_PREFIXES,
    _add_unique,
    _apply_evaluation,
    _debug,
    _evidence_id,
    _last_ai_with_calls_index,
    _latest_round,
    _next_hypothesis,
    _status_for,
    _text,
    _update_notebook,
    _compress_history,
)
from .can_conclude import can_conclude
from .reject_conclusion import reject_conclusion
from .prepare_case import prepare_case
from .generate_hypotheses import generate_hypotheses as _generate_hypotheses
from .choose_action import choose_action as _choose_action
from .run_tools import _execute, run_tools as _run_tools
from .evaluate_result import evaluate_result as _evaluate_result
from .generate_final_report import generate_final_report as _generate_final_report
from .collect_configuration import collect_configuration
from .validate_project import validate_project
from .request_db_setup import request_db_setup
from .validate_database import validate_database
from .configuration_confirmation import configuration_confirmation
from .configuration_error import configuration_error
from agent.state import InvestigationState
from .common import RunnableConfig

llm = common.llm
llm_with_tools = common.llm_with_tools
_tools_by_name = _run_tools_module._tools_by_name


def generate_hypotheses(state: InvestigationState) -> dict:
    common.llm = llm
    return _generate_hypotheses(state)


def choose_action(state: InvestigationState) -> dict:
    common.llm_with_tools = llm_with_tools
    return _choose_action(state)


def run_tools(
    state: InvestigationState,
    config: RunnableConfig,
    runtime: common.Runtime[common.DetectiveContext] | None = None,
) -> dict:
    _run_tools_module._tools_by_name = _tools_by_name
    return _run_tools(state, config, runtime)


def evaluate_result(state: InvestigationState) -> dict:
    common.llm = llm
    return _evaluate_result(state)


def generate_final_report(state: InvestigationState) -> dict:
    common.llm = llm
    return _generate_final_report(state)
