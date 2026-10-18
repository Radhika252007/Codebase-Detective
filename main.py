from uuid import uuid4
from agent.graph import workflow
from case_brief import build_case_brief


def main():
    # Use a unique thread ID for this investigation
    config = {
        "configurable": {
            "thread_id": f"ticketly-pagination-{uuid4()}"
        }
    }
    case_brief = build_case_brief()
    # Bug report for Codebase Detective
    initial_state = {
        "bug_report": """
        In Ticketly, the GET /api/bookings/me endpoint becomes slower
        as a user accumulates more bookings.

        Investigate the route, controller, and service implementation.
        Check whether the Prisma query retrieves all of a user's
        bookings or applies a result limit or pagination. Examine
        related-data loading and Redis caching if relevant.

        Identify the most likely cause using direct evidence from
        the code. Do not assume pagination is missing. If the
        implementation does not confirm the suspected issue, continue
        investigating other plausible causes and clearly distinguish
        confirmed findings from unverified hypotheses.
        """,
        "case_brief" : case_brief,
        "hypotheses": [],
        "files_inspected": [],
        "items_inspected":  [],
        "dead_ends": [],
        "current_hypothesis": "",
        "evidence": [],
        "steps_taken": 0,
        "root_cause": "",
        "final_report": "",
    }

    print("=" * 60)
    print("       CODEBASE DETECTIVE - INVESTIGATION STARTED")
    print("=" * 60)

    # Run the graph once
    workflow.invoke(initial_state, config=config)

    # Read the final persisted state without rerunning the graph
    state = workflow.get_state(config)

    if not state or not state.values:
        print("No investigation state found.")
        return

    values = state.values

    print(f"\nThread ID: {config['configurable']['thread_id']}")
    print(f"Steps taken: {values.get('steps_taken', 0)}")

    print("\n" + "=" * 60)
    print("HYPOTHESES")
    print("=" * 60)

    for hypothesis in values.get("hypotheses", []):
        print(hypothesis)

    print("\n" + "=" * 60)
    print("EVIDENCE")
    print("=" * 60)

    for item in values.get("evidence", []):
        print(item)

    print("\n" + "=" * 60)
    print("ROOT CAUSE")
    print("=" * 60)
    print(values.get("root_cause", "Not determined"))

    print("\n" + "=" * 60)
    print("FINAL REPORT")
    print("=" * 60)

    report = values.get("final_report", "")

    # Gemini may return text as content blocks rather than a string
    if isinstance(report, list):
        for block in report:
            if isinstance(block, dict) and block.get("type") == "text":
                print(block.get("text", ""))
            elif isinstance(block, str):
                print(block)
    else:
        print(report or "No final report generated.")

    print("\n" + "=" * 60)
    print("INVESTIGATION COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
