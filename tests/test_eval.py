import json

from agent.eval import EVAL_PATH, load_cases, run_eval
from agent.llm import FakeLLM
from agent.models import Inquiry
from agent.tools import ToolBox


def test_eval_set_is_well_formed() -> None:
    cases = load_cases(EVAL_PATH)
    assert len(cases) >= 20
    for case in cases:
        Inquiry.model_validate(case["inquiry"])
    assert len({c["label"] for c in cases}) == 5


def test_fake_llm_eval_accuracy_floor() -> None:
    names = [p["name"] for p in ToolBox().crm["properties"]]
    report = run_eval(FakeLLM(names), load_cases())
    assert report["accuracy"] >= 0.8, json.dumps(report, indent=2)
