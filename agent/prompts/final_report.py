def build_prompt(
    bug_report: str,
    hypotheses: list,
    evidence: list,
    evaluation: str,
) -> str:
    return f"""
You are AI Codebase Detective.
Generate a final report for the investigation.

Bug report:
{bug_report}

Hypotheses and their current statuses:
{hypotheses}

Evidence collected:
{evidence}

Latest evaluation:
{evaluation}

Write a report with these sections:

1. Problem Summary
2. Most Likely Root Cause
3. Supporting Evidence
4. Other Hypotheses and Their Status
5. Recommended Next Steps

Rules:
- Base conclusions only on the evidence provided.
- Do not claim a root cause is confirmed unless the evidence supports confirmation.
- Clearly identify hypotheses that remain untested or inconclusive.
- If the investigation found insufficient evidence, state that honestly.
- Do not invent tool results, measurements, or code findings.
"""