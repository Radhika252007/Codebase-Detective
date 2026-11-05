import re

from .common import Evaluation, MAX_HYPOTHESES, MIN_TOOLS_TO_CONFIRM, OLD_RESULT_CHARS
from .common import DEBUG
from langchain_core.messages import ToolMessage


def _text(content) -> str:
    """Gemini can return a list of content blocks instead of a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type", "text") == "text":
                parts.append(part.get("text", ""))
        return "".join(parts)
    return str(content)


def _add_unique(items: list, value: str) -> list:
    if value and value not in items:
        items.append(value)
    return items


def _debug(title: str, *lines) -> None:
    if DEBUG:
        print(f"\n--- {title} ---")
        for line in lines:
            print(line)


def _last_ai_with_calls_index(messages: list) -> int:
    for i in range(len(messages) - 1, -1, -1):
        if getattr(messages[i], "tool_calls", None):
            return i
    return -1


def _latest_round(messages: list):
    """(AI message that made the calls, the ToolMessages that answered it)."""
    i = _last_ai_with_calls_index(messages)
    if i < 0:
        return None, []
    return messages[i], [m for m in messages[i + 1:] if isinstance(m, ToolMessage)]


def _compress_history(messages: list, evidence: list) -> list:
    """
    Keep the newest tool outputs in full; shorten older ones to their verdict plus a short
    excerpt. tool_call_ids are preserved, so the call/response pairing stays valid.
    """
    by_call = {e.get("tool_call_id"): e for e in evidence}
    cut = _last_ai_with_calls_index(messages)
    out = []
    for i, m in enumerate(messages):
        if isinstance(m, ToolMessage) and i < cut:
            e = by_call.get(m.tool_call_id)
            if e:
                note = (f"[{e['id']} shortened] verdict: {e['evaluation']} | "
                        f"output start: {e['output_summary'][:OLD_RESULT_CHARS]}")
            else:
                note = f"[shortened] {_text(m.content)[:OLD_RESULT_CHARS]}"
            out.append(ToolMessage(content=note, tool_call_id=m.tool_call_id, name=getattr(m, "name", None)))
        else:
            out.append(m)
    return out


# ---------------------------------------------------------
# Detective's notebook
# ---------------------------------------------------------
_HEADER_RE = re.compile(r"^== \w+ \S+ -- (.+?):\d+-\d+ ", re.M)
_DEAD_END_PREFIXES = ("ERROR", "No definition of", "No matches found", "No indexes found", "No Prisma or raw-SQL")


def _update_notebook(tool_name, tool_args, tool_output, files_inspected, items_inspected, dead_ends) -> dict:
    name, args, output = tool_name.lower(), tool_args or {}, str(tool_output)

    if name == "read_file":                                   # parameter is relative_path
        _add_unique(files_inspected, str(args.get("relative_path", "")))
    elif name == "view_code_item":                            # real files are in the output headers
        _add_unique(items_inspected, str(args.get("name", "")))
        for path in _HEADER_RE.findall(output):
            _add_unique(files_inspected, path)
    elif name == "function_history":
        _add_unique(items_inspected, f"history:{args.get('name', '')}")
    elif name == "inspect_table":
        _add_unique(items_inspected, f"table:{args.get('table_name', '')}")
    elif name == "profile_endpoint":
        _add_unique(items_inspected, f"endpoint:{args.get('path', '')}")
    elif name == "scan_db_calls":
        _add_unique(items_inspected, f"db-calls:{args.get('path_hint', '') or 'all'}")

    if output.lstrip().startswith(_DEAD_END_PREFIXES):
        _add_unique(dead_ends, f"{tool_name}({args})")

    return {"files_inspected": files_inspected, "items_inspected": items_inspected, "dead_ends": dead_ends}


# ---------------------------------------------------------
# Hypothesis bookkeeping (plain Python, the LLM never edits this)
# ---------------------------------------------------------
def _evidence_id(line: str) -> str:
    return line.split(":", 1)[0].strip()


def _status_for(h: dict, by_id: dict) -> str:
    sup = [_evidence_id(s) for s in h["supporting_evidence"]]
    con = [_evidence_id(s) for s in h["contradicting_evidence"]]
    if con and len(con) > len(sup):
        return "rejected"
    if sup:
        tools = {by_id[i]["tool"] for i in sup if i in by_id}
        explains = any(by_id[i]["explains_full_symptom"] for i in sup if i in by_id)
        if explains and len(tools) >= MIN_TOOLS_TO_CONFIRM:
            return "confirmed"
        return "supported"
    return "untested"


def _apply_evaluation(hypotheses: list, entry: dict, ev: Evaluation, evidence_so_far: list) -> list:
    hyps = [{**h,
             "supporting_evidence": list(h["supporting_evidence"]),
             "contradicting_evidence": list(h["contradicting_evidence"])} for h in hypotheses]
    index = {h["id"].upper(): h for h in hyps}

    for eff in ev.effects:
        h = index.get(eff.hypothesis_id.strip().upper())
        if h is None:
            continue
        line = f"{entry['id']}: {eff.reason}"
        if eff.effect == "supports":
            h["supporting_evidence"].append(line)
        elif eff.effect == "rejects":
            h["contradicting_evidence"].append(line)

    new = (ev.new_hypothesis or "").strip()
    if new and len(hyps) < MAX_HYPOTHESES and not any(new.lower() == h["description"].strip().lower() for h in hyps):
        hyps.append({"id": f"H{len(hyps) + 1}", "description": new, "status": "untested",
                     "supporting_evidence": [], "contradicting_evidence": []})

    by_id = {e["id"]: e for e in [*evidence_so_far, entry]}
    for h in hyps:
        h["status"] = _status_for(h, by_id)
    return hyps


def _next_hypothesis(hyps: list) -> str:
    for status in ("supported", "untested", "confirmed"):
        for h in hyps:
            if h["status"] == status:
                return h["id"]
    return hyps[0]["id"] if hyps else ""


# ---------------------------------------------------------
# The gate: the LLM proposes to stop, code decides
# ---------------------------------------------------------
