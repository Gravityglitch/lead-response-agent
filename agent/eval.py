"""Classification eval against evals/inquiries.jsonl.

uv run python -m agent.eval            # FakeLLM, offline
uv run python -m agent.eval --live     # Claude (needs ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from agent.llm import LLM, ClaudeLLM, FakeLLM
from agent.models import Category, Inquiry
from agent.tools import ToolBox

EVAL_PATH = Path(__file__).resolve().parent.parent / "evals" / "inquiries.jsonl"


def load_cases(path: Path = EVAL_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_eval(llm: LLM, cases: list[dict]) -> dict:
    per_cat: dict[str, Counter] = defaultdict(Counter)
    confusion: Counter = Counter()
    correct = 0
    for case in cases:
        inquiry = Inquiry.model_validate(case["inquiry"])
        predicted = llm.classify(inquiry).category
        expected = Category(case["label"])
        per_cat[expected]["total"] += 1
        if predicted == expected:
            correct += 1
            per_cat[expected]["correct"] += 1
        else:
            confusion[(expected, predicted)] += 1
    return {
        "n": len(cases),
        "correct": correct,
        "accuracy": correct / len(cases) if cases else 0.0,
        "per_category": {
            c: {"correct": v["correct"], "total": v["total"]} for c, v in sorted(per_cat.items())
        },
        "confusions": [
            {"expected": e, "predicted": p, "count": n} for (e, p), n in confusion.most_common()
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="use Claude instead of FakeLLM")
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    args = parser.parse_args()

    toolbox = ToolBox()
    names = [p["name"] for p in toolbox.crm["properties"]]
    llm: LLM = ClaudeLLM() if args.live else FakeLLM(names)
    report = run_eval(llm, load_cases())

    if args.json:
        print(json.dumps(report, indent=2))
        return
    print(f"backend: {type(llm).__name__}")
    print(f"accuracy: {report['correct']}/{report['n']} = {report['accuracy']:.1%}\n")
    for cat, v in report["per_category"].items():
        print(f"  {cat:<18} {v['correct']}/{v['total']}")
    if report["confusions"]:
        print("\nconfusions (expected -> predicted):")
        for c in report["confusions"]:
            print(f"  {c['expected']} -> {c['predicted']}: {c['count']}")


if __name__ == "__main__":
    main()
