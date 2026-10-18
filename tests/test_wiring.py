"""
Offline test of the investigation graph: fake LLM, fake tools, in-memory checkpointer.
No API key, database or network needed. Run from the project root:

    python tests/test_flow.py

It checks the wiring and the logic around the model (hypothesis tracking, the conclusion
gate, step limit, call/result pairing, duplicate skipping, history trimming). It cannot
tell you how well Gemini reasons; that is what a planted-bug harness measures.
"""
import itertools
import os
import sys
import types

os.environ["DETECTIVE_DB"] = ":memory:"
sys.path.insert(0, os.getcwd())

# ---- hermetic imports: replace everything that needs keys, a database or the network ----
class _Dummy:
    def bind_tools(self, tools):
        return self

_genai = types.ModuleType("langchain_google_genai")
_genai.ChatGoogleGenerativeAI = lambda **kw: _Dummy()
sys.modules["langchain_google_genai"] = _genai

_brief = types.ModuleType("case_brief")
_brief.build_case_brief = lambda: "BRIEF: Express + Prisma; table Order has no index besides its primary key"
sys.modules["case_brief"] = _brief

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402


@tool
def view_code_item(name: str) -> str:
    """Show a function."""
    return f"definition of {name}"


@tool
def inspect_table(table_name: str) -> str:
    """Inspect a table."""
    return f"table {table_name}: 5,000 rows"


@tool
def profile_endpoint(path: str) -> str:
    """Profile an endpoint."""
    return f"GET {path}: 4100 ms, 51 queries per request"


@tool
def scan_db_calls(path_hint: str = "") -> str:
    """Scan database calls."""
    return f"order.findMany ... orderItem.findMany INSIDE LOOP ({path_hint})"


@tool
def read_file(relative_path: str) -> str:
    """Read a file."""
    return f"contents of {relative_path}"


@tool
def db_activity() -> str:
    """Always crashes (used to test error handling)."""
    raise RuntimeError("connection refused")


FAKE_TOOLS = [view_code_item, inspect_table, profile_endpoint, scan_db_calls, read_file, db_activity]
_pkg = types.ModuleType("agent.codebase_detective_tools")
_pkg.__path__ = []
_mod = types.ModuleType("agent.codebase_detective_tools.tools")
_mod.ALL_TOOLS = FAKE_TOOLS
sys.modules["agent.codebase_detective_tools"] = _pkg
sys.modules["agent.codebase_detective_tools.tools"] = _mod

from agent import graph, nodes  # noqa: E402


# ---- a scripted fake model ----
class Script:
    def __init__(self, hyps, turns, evals=(), final="FINAL REPORT"):
        self.hyps, self.turns, self.evals, self.final = list(hyps), list(turns), list(evals), final


class FakeLLM:
    def __init__(self, script):
        self.script, self.sent = script, []

    def bind_tools(self, tools):
        return _Bound(self)

    def with_structured_output(self, schema):
        return _Structured(self, schema)

    def invoke(self, prompt):
        self.sent.append(("text", prompt))
        return AIMessage(content=self.script.final)


class _Bound:
    def __init__(self, llm):
        self.llm = llm

    def invoke(self, messages):
        self.llm.sent.append(("tools", messages))
        return self.llm.script.turns.pop(0)


class _Structured:
    def __init__(self, llm, schema):
        self.llm, self.schema = llm, schema

    def invoke(self, prompt):
        self.llm.sent.append(("structured", prompt))
        if self.schema.__name__ == "GeneratedHypothesisList":
            return nodes.GeneratedHypothesisList(
                hypotheses=[nodes.GeneratedHypothesis(description=d) for d in self.llm.script.hyps])
        item = self.llm.script.evals.pop(0) if self.llm.script.evals else {}
        return nodes.Evaluation(**item)


def install(script):
    fake = FakeLLM(script)
    nodes.llm = fake
    nodes.llm_with_tools = fake.bind_tools(None)
    nodes._tools_by_name = {t.name: t for t in FAKE_TOOLS}
    return fake


_ids = itertools.count(1)


def calls(*specs):
    return AIMessage(content="", tool_calls=[
        {"name": n, "args": a, "id": f"call_{next(_ids)}", "type": "tool_call"} for n, a in specs])


def say(text):
    return AIMessage(content=text)


def sup(h, reason="evidence"):
    return {"hypothesis_id": h, "effect": "supports", "reason": reason}


def rej(h, reason="evidence"):
    return {"hypothesis_id": h, "effect": "rejects", "reason": reason}


def status(final_state):
    return {h["id"]: h["status"] for h in final_state["hypotheses"]}


# ---------------------------------------------------------------- tests
def test_happy_path():
    install(Script(
        hyps=["Missing index on Order.userId", "N+1 query in getOrders", "Connection pool too small"],
        turns=[calls(("profile_endpoint", {"path": "/api/orders?userId=1"})),
               calls(("scan_db_calls", {"path_hint": "orders"})),
               say("H2: N+1 in getOrders")],
        evals=[
            {"effects": [sup("H1"), sup("H2"), rej("H3")], "explains_full_symptom": False,
             "summary": "51 queries per request"},
            {"effects": [sup("H2")], "explains_full_symptom": True, "summary": "query inside a loop"},
        ]))
    s = graph.investigate("Orders endpoint takes 4 s")
    assert status(s) == {"H1": "supported", "H2": "confirmed", "H3": "rejected"}, status(s)
    assert s["steps_taken"] == 3 and s["stop_reason"] == "concluded"
    assert s["root_cause"].startswith("H2") and s["final_report"] == "FINAL REPORT"
    assert [e["id"] for e in s["evidence"]] == ["E1", "E2"]
    assert "endpoint:/api/orders?userId=1" in s["items_inspected"]


def test_gate_pushes_back_then_allows():
    fake = install(Script(
        hyps=["A", "B"],
        turns=[say("done already"),
               calls(("profile_endpoint", {"path": "/x"})), say("done"),
               calls(("scan_db_calls", {"path_hint": "x"})), say("done")],
        evals=[{"effects": [sup("H1")], "explains_full_symptom": True, "summary": "s1"},
               {"effects": [sup("H1")], "explains_full_symptom": True, "summary": "s2"}]))
    s = graph.investigate("slow")
    assert s["conclusion_rejections"] == 2 and status(s)["H1"] == "confirmed"
    assert s["steps_taken"] == 5
    tool_calls_sent = [m for kind, m in fake.sent if kind == "tools"]
    assert "NOTE FROM THE INVESTIGATION CONTROLLER" in tool_calls_sent[1][-1].content
    assert "NOTE FROM THE INVESTIGATION CONTROLLER" not in tool_calls_sent[2][-1].content  # one-shot


def test_gate_gives_up_after_two_pushbacks():
    install(Script(hyps=["A"], turns=[say("x"), say("x"), say("x")]))
    s = graph.investigate("slow")
    assert s["conclusion_rejections"] == 2 and s["stop_reason"] == "unconfirmed"
    assert s["steps_taken"] == 3 and s["final_report"] == "FINAL REPORT"


def test_step_limit_is_graceful():
    install(Script(hyps=["A"], turns=[calls(("read_file", {"relative_path": f"f{i}.js"})) for i in range(30)]))
    s = graph.investigate("slow", recursion_limit=60)
    assert s["steps_taken"] == nodes.MAX_STEPS and s["stop_reason"] == "step_limit"
    assert len(s["evidence"]) == nodes.MAX_STEPS and s["final_report"]
    assert "f0.js" in s["files_inspected"]            # read_file's real argument name is recorded


def test_default_recursion_limit_info():
    install(Script(hyps=["A"], turns=[calls(("read_file", {"relative_path": f"f{i}.js"})) for i in range(30)]))
    try:
        graph.investigate("slow", recursion_limit=25)
        print("  info: recursion_limit=25 was enough in your LangGraph version")
    except Exception as exc:                                  # GraphRecursionError
        print(f"  info: recursion_limit=25 raises {type(exc).__name__} on a full 8-step run -> keep 60")


def _hyps():
    return [{"id": f"H{i}", "description": d, "status": "untested",
             "supporting_evidence": [], "contradicting_evidence": []} for i, d in ((1, "A"), (2, "B"))]


def test_parallel_calls_pair_correctly_and_duplicates_are_skipped():
    install(Script(hyps=[], turns=[], evals=[
        {"effects": [sup("H1")], "summary": "first"}, {"effects": [sup("H2")], "summary": "second"}]))
    ai = calls(("view_code_item", {"name": "getOrders"}), ("inspect_table", {"table_name": "Order"}))
    state = {"bug_report": "slow", "hypotheses": _hyps(), "evidence": [], "messages": [HumanMessage("go"), ai]}

    out = nodes.run_tools(state, {})
    assert [m.tool_call_id for m in out["messages"]] == [c["id"] for c in ai.tool_calls]
    state["messages"] = [*state["messages"], *out["messages"]]
    ev = nodes.evaluate_result(state)
    assert [(e["tool"], e["output_summary"]) for e in ev["evidence"]] == [
        ("view_code_item", "definition of getOrders"), ("inspect_table", "table Order: 5,000 rows")]
    assert status({"hypotheses": ev["hypotheses"]}) == {"H1": "supported", "H2": "supported"}

    # second round: one exact repeat + one new call
    ai2 = calls(("view_code_item", {"name": "getOrders"}), ("read_file", {"relative_path": "x.js"}))
    state.update(evidence=ev["evidence"], hypotheses=ev["hypotheses"], messages=[*state["messages"], ai2])
    out2 = nodes.run_tools(state, {})
    assert out2["messages"][0].content.startswith(nodes.DUPLICATE_PREFIX)
    assert out2["messages"][1].content == "contents of x.js"
    state["messages"] = [*state["messages"], *out2["messages"]]
    ev2 = nodes.evaluate_result(state)
    assert len(ev2["evidence"]) == 3                     # the duplicate was not evaluated again


def test_tool_errors_do_not_crash_the_run():
    install(Script(hyps=["A"], turns=[calls(("db_activity", {}), ("no_such_tool", {"x": 1})), say("stop"), say("stop"), say("stop")]))
    s = graph.investigate("slow")
    contents = [m.content for m in s["messages"] if isinstance(m, ToolMessage)]
    assert any("ERROR: db_activity failed: RuntimeError" in c for c in contents), contents
    assert any("unknown tool 'no_such_tool'" in c for c in contents), contents
    assert len(s["dead_ends"]) == 2 and s["stop_reason"] == "unconfirmed"


def test_old_tool_outputs_are_shortened():
    c1, c2 = calls(("view_code_item", {"name": "a"})), calls(("view_code_item", {"name": "b"}))
    msgs = [HumanMessage("go"), c1, ToolMessage(content="x" * 5000, tool_call_id=c1.tool_calls[0]["id"]),
            c2, ToolMessage(content="y" * 5000, tool_call_id=c2.tool_calls[0]["id"])]
    evidence = [{"id": "E1", "tool_call_id": c1.tool_calls[0]["id"], "tool": "view_code_item", "input": {},
                 "output_summary": "x" * 1000, "evaluation": "VERDICT", "effects": [],
                 "explains_full_symptom": False}]
    out = nodes._compress_history(msgs, evidence)
    assert len(out[2].content) < 700 and "E1" in out[2].content and "VERDICT" in out[2].content
    assert out[2].tool_call_id == c1.tool_calls[0]["id"] and len(out[4].content) == 5000


def test_notebook():
    files, items, dead = [], [], []
    nodes._update_notebook("read_file", {"relative_path": "src/a.js"}, "1: code", files, items, dead)
    nodes._update_notebook("view_code_item", {"name": "getOrders", "path_hint": "orders"},
                           "== function getOrders -- src/orders/orders.service.js:3-9 (7 lines) ==\n3: x",
                           files, items, dead)
    nodes._update_notebook("view_code_item", {"name": "nope"}, "No definition of 'nope' found.", files, items, dead)
    assert files == ["src/a.js", "src/orders/orders.service.js"], files
    assert items == ["getOrders", "nope"] and dead == ["view_code_item({'name': 'nope'})"], (items, dead)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("ALL PASSED")