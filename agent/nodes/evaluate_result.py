from agent.state import InvestigationState
from . import common
from .common import (
    EVALUATION_FORMAT,
    Evaluation,
    DUPLICATE_PREFIX,
    SUMMARY_CHARS,
    build_evaluation_prompt,
    log,
)
from .helpers import (
    _apply_evaluation,
    _debug,
    _latest_round,
    _next_hypothesis,
    _text,
    _update_notebook,
)


def evaluate_result(state: InvestigationState) -> dict:
    ai_message, results = _latest_round(state["messages"])
    if ai_message is None or not results:
        return {"evaluation": "INCONCLUSIVE: no tool result found."}

    structured = common.llm.with_structured_output(Evaluation)
    hypotheses = state.get("hypotheses", [])
    evidence = list(state.get("evidence", []))
    files = list(state.get("files_inspected", []))
    items = list(state.get("items_inspected", []))
    dead_ends = list(state.get("dead_ends", []))
    summaries = []

    for result in results:                       # every result of the round, each with ITS OWN call
        content = _text(result.content)
        if content.startswith(DUPLICATE_PREFIX):
            summaries.append(content)
            continue
        call = next((c for c in ai_message.tool_calls if c["id"] == result.tool_call_id), None)
        tool_name = call["name"] if call else (getattr(result, "name", None) or "unknown")
        tool_args = call["args"] if call else {}

        prompt = build_evaluation_prompt(
            state["bug_report"], hypotheses, state.get("current_hypothesis", ""),
            tool_name, tool_args, content,
        ) + EVALUATION_FORMAT
        try:
            ev = structured.invoke(prompt)
        except Exception as exc:                 # a failed evaluation must not kill the investigation
            log.warning("evaluation failed for %s: %s", tool_name, exc)
            ev = None
        if ev is None:
            ev = Evaluation(summary="Evaluation failed; treat this result as inconclusive.")

        verdicts = "; ".join(f"{e.hypothesis_id} {e.effect}" for e in ev.effects) or "no hypothesis affected"
        entry = {
            "id": f"E{len(evidence) + 1}",
            "tool": tool_name,
            "tool_call_id": result.tool_call_id,
            "input": tool_args,
            "output_summary": content[:SUMMARY_CHARS],
            "evaluation": f"{ev.summary} | {verdicts} | explains full symptom: "
                          f"{'yes' if ev.explains_full_symptom else 'no'}",
            "effects": [e.model_dump() for e in ev.effects],
            "explains_full_symptom": ev.explains_full_symptom,
        }
        hypotheses = _apply_evaluation(hypotheses, entry, ev, evidence)
        evidence.append(entry)
        _update_notebook(tool_name, tool_args, content, files, items, dead_ends)
        summaries.append(f"{entry['id']} ({tool_name}): {entry['evaluation']}")

    _debug("EVALUATION", *summaries)
    _debug("DETECTIVE'S NOTEBOOK", f"files: {files}", f"items: {items}", f"dead ends: {dead_ends}")
    _debug("HYPOTHESES", *[f"{h['id']} [{h['status']}] {h['description']}" for h in hypotheses])

    return {
        "evaluation": "\n".join(summaries),
        "evidence": evidence,
        "hypotheses": hypotheses,
        "current_hypothesis": _next_hypothesis(hypotheses),
        "files_inspected": files,
        "items_inspected": items,
        "dead_ends": dead_ends,
    }
