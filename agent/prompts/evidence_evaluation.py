def build_prompt(
    bug_report: str,
    hypotheses: list,
    current_hypothesis: str,
    tool_name: str,
    tool_args: dict,
    tool_result: str,
) -> str:
    return f"""
    You are the evidence evaluator for an AI Codebase Detective.

    Bug report: {bug_report}

    Hypotheses: {hypotheses}

    Current hypothesis:
{current_hypothesis or 'Not explicitly selected yet'}

Tool executed:
{tool_name}

Tool arguments:
{tool_args}

Tool result:
{tool_result}

Evaluate what this result tells us about the bug.

Use exactly this format:

Decision: SUPPORTS, REJECTS, or INCONCLUSIVE
Reason: Explain what the evidence establishes and what it does not establish.
Next step: Suggest the most useful investigation action.

Rules:
- Tool errors are not evidence that a hypothesis is true or false.
- Do not claim a root cause is confirmed without sufficient evidence.
- A result may support a hypothesis without proving it.
- Base your conclusion on the actual tool result, not assumptions.
"""