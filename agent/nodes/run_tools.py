from agent.state import InvestigationState
from agent.runtime_context import DetectiveContext
from langgraph.prebuilt import ToolRuntime
from langgraph.runtime import Runtime
from .common import DUPLICATE_PREFIX, REPEATABLE_TOOLS, RunnableConfig, ToolMessage, ALL_TOOLS

_tools_by_name = {tool.name: tool for tool in ALL_TOOLS}

def _execute(
    call: dict,
    config: RunnableConfig,
    state: InvestigationState,
    runtime: Runtime[DetectiveContext] | None = None,
) -> ToolMessage:
    """Run one tool call. Always returns a ToolMessage, even for unknown tools or tool crashes."""
    name, call_id = call["name"], call["id"]
    tool_obj = _tools_by_name.get(name)
    if tool_obj is None:
        return ToolMessage(content=f"ERROR: unknown tool '{name}'. Available: {sorted(_tools_by_name)}",
                           tool_call_id=call_id, name=name, status="error")
    try:
        tool_call = {"name": name, "args": call["args"], "id": call_id, "type": "tool_call"}
        runtime_fields = getattr(tool_obj.args_schema, "model_fields", {})
        if "runtime" in runtime_fields:
            if runtime is None:
                raise RuntimeError("No per-investigation runtime context is available.")
            tool_runtime = ToolRuntime(
                state=state,
                context=runtime.context,
                config=config,
                stream_writer=runtime.stream_writer,
                tool_call_id=call_id,
                store=runtime.store,
                tools=list(_tools_by_name.values()),
                execution_info=runtime.execution_info,
                server_info=runtime.server_info,
            )
            tool_call["args"] = {**call["args"], "runtime": tool_runtime}
            return tool_obj.invoke(tool_call, config)
        return tool_obj.invoke(tool_call, config)
    except Exception as exc:   # a crashing tool is evidence too, not a reason to stop the run
        detail = type(exc).__name__
        return ToolMessage(content=f"ERROR: {name} failed: {detail}"[:500],
                           tool_call_id=call_id, name=name, status="error")



def run_tools(
    state: InvestigationState,
    config: RunnableConfig,
    runtime: Runtime[DetectiveContext] | None = None,
) -> dict:
    last = state["messages"][-1]
    evidence = state.get("evidence", [])

    out = []
    for call in last.tool_calls:                # same order as the calls, so ids always pair up
        prior = next((e for e in evidence if e["tool"] == call["name"] and e["input"] == call["args"]), None)
        if prior and call["name"] not in REPEATABLE_TOOLS:
            out.append(ToolMessage(
                content=(f"{DUPLICATE_PREFIX} {call['name']} was already run with these exact arguments "
                         f"({prior['id']}: {prior['evaluation']}). Choose a different action."),
                tool_call_id=call["id"], name=call["name"]))
        else:
            out.append(_execute(call, config, state, runtime))
    return {"messages": out}
