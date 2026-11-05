from agent.state import InvestigationState
from . import common
from .common import (
    ACCESS_PLAYBOOK,
    GeneratedHypothesisList,
    INVESTIGATION_PLAYBOOK,
    hypothesis_generation_template,
)


def generate_hypotheses(state: InvestigationState) -> dict:
    structured_llm = common.llm.with_structured_output(GeneratedHypothesisList)

    prompt = (
        hypothesis_generation_template.format(bug_report=state["bug_report"])
        + "\n\nFACTS ABOUT THE PROJECT (make hypotheses specific to them; do not assume problems "
          "these facts contradict):\n" + state.get("case_brief", "")
        + "\n\n" + INVESTIGATION_PLAYBOOK + "\n" + ACCESS_PLAYBOOK
        + "\nUse the playbook section that matches the question and ignore the other."
        + "\nGive 3 to 5 hypotheses. Each must be testable with one of the available tools. "
          "Do not state conclusions."
    )
    result = structured_llm.invoke(prompt)

    descriptions = [h.description.strip() for h in (result.hypotheses if result else []) if h.description.strip()]
    descriptions = descriptions[:5] or [
        "The cause is not identified yet; start from the code path of the reported feature."
    ]
    hypotheses = [
        {"id": f"H{i}", "description": d, "status": "untested",
         "supporting_evidence": [], "contradicting_evidence": []}
        for i, d in enumerate(descriptions, start=1)
    ]
    return {"hypotheses": hypotheses, "current_hypothesis": hypotheses[0]["id"]}
