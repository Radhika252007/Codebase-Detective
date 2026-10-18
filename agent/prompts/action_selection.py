def build_system_prompt() -> str:
    return """
    You are AI Codebase Detective, an evidence-driven debugging agent.

Your task is to investigate the reported bug using the available tools.

Rules:
1. Review the complete investigation history and hypothesis ledger.
2. Choose only ONE useful tool call per turn.
3. Prefer actions that gather concrete evidence.
4. Do not repeat an unsuccessful tool call without a reason.
5. Discover and verify endpoint paths from source code before timing them.
6. Use database tools only for relevant, read-only investigation.
7. Never invent tool results or claim an unverified root cause.
8. If enough evidence has been collected, stop calling tools and
   provide a concise conclusion.
9. Follow the tool safety restrictions.
"""



def build_initial_investigation_prompt(
    bug_report: str,
    hypotheses: list,
    case_brief: str,
    files_inspected: list,
    items_inspected: list,
    dead_ends: list,
) -> str:
    return f"""
    Bug report: {bug_report}

    Hypotheses: {hypotheses}

    Case Brief:
    {case_brief}

    Detective's Notebook:
    Files inspected: {files_inspected}
    Functions/classes inspected: {items_inspected}
    Dead ends: {dead_ends}

    Review the notebook before choosing your next action.
    Start the investigation by selecting the most useful action.
    """


def build_follow_up_prompt(
    evaluation: str,
    hypotheses: list,
    evidence: list,
    files_inspected: list,
    items_inspected: list,
    dead_ends: list,
) -> str:
    return f"""
    Latest evaluation: {evaluation}

    Current hypothesis ledger: {hypotheses}
    Evidence collected: {evidence}

    Detective's Notebook:
    Files inspected: {files_inspected}
    Functions/classes inspected: {items_inspected}
    Dead ends: {dead_ends}

    Review the notebook and complete investigation history.
    Avoid repeating actions that produced no new information.
    Choose the next useful investigation action.
    If sufficient direct evidence exists, stop using tools.
    """
