"""
Run one Codebase Detective investigation from the terminal.

    python run.py -f query.txt                  # question from a file (easiest for long questions)
    python run.py "GET /api/orders takes 4 s"   # question on the command line (quote it)
    python run.py -f query.txt --debug          # print hypotheses, evaluations, notebook each round
    python run.py -f query.txt --save report.md # also write the report and run summary to a file

Run it from the project root (the folder that contains config.py, case_brief.py, tools/ and agent/).
"""
import argparse
import json
import os
import sys


def _summary(state: dict) -> str:
    lines = [
        f"steps: {state.get('steps_taken', '?')} | stop reason: {state.get('stop_reason', '?')} "
        f"| pushbacks: {state.get('conclusion_rejections', 0)}",
        "hypotheses:",
    ]
    for h in state.get("hypotheses", []):
        lines.append(f"  {h['id']} [{h['status']}] {h['description']}")
    lines.append("evidence (tools the agent used):")
    for e in state.get("evidence", []):
        lines.append(f"  {e['id']} {e['tool']} {json.dumps(e['input'])[:90]}")
    if not state.get("evidence"):
        lines.append("  none")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run one Codebase Detective investigation.")
    parser.add_argument("question", nargs="?", help="the bug report or question (quote it)")
    parser.add_argument("-f", "--file", help="read the question from this text file")
    parser.add_argument("--debug", action="store_true", help="print internal state after every round")
    parser.add_argument("--save", metavar="FILE", help="write the report and run summary to this file")
    args = parser.parse_args(argv)

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            question = fh.read().strip()
    else:
        question = (args.question or "").strip()
    if not question:
        parser.error("give a question, or use -f query.txt")

    if hasattr(sys.stdout, "reconfigure"):                     # Windows consoles: avoid UnicodeEncodeError
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.debug:
        os.environ["DETECTIVE_DEBUG"] = "1"                    # must be set BEFORE agent.nodes is imported

    from agent.graph import investigate                        # imported late so --debug takes effect

    print(f"Investigating ({len(question)} characters). This usually takes 1-3 minutes...\n")
    state = investigate(question)

    report = state.get("final_report", "(no report produced)")
    summary = _summary(state)
    print("=" * 20, "FINAL REPORT", "=" * 20)
    print(report)
    print("\n" + "=" * 20, "RUN SUMMARY", "=" * 20)
    print(summary)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as fh:
            fh.write(f"# Question\n\n{question}\n\n# Final report\n\n{report}\n\n# Run summary\n\n```\n{summary}\n```\n")
        print(f"\nSaved to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())