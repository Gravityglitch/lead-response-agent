"""Exercise ClaudeLLM's tool loop against a scripted stand-in for the Anthropic client."""

from types import SimpleNamespace

from agent.llm import ClaudeLLM
from agent.models import Category, Classification, DraftReply, Inquiry
from agent.tools import ToolBox


class ScriptedMessages:
    def __init__(self, responses: list) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _client(responses: list):
    return SimpleNamespace(messages=ScriptedMessages(responses))


def test_classify_returns_parsed_output() -> None:
    expected = Classification(category=Category.PRICING, confidence=0.9, rationale="rent asked")
    llm = ClaudeLLM(
        client=_client([SimpleNamespace(stop_reason="end_turn", parsed_output=expected)])
    )
    inq = Inquiry.model_validate({"channel": "sms", "from": "+1", "body": "rent?"})
    assert llm.classify(inq) == expected
    assert llm.client.messages.calls[0]["output_format"] is Classification


def test_draft_runs_tool_loop_and_feeds_results_back() -> None:
    tool_call = SimpleNamespace(
        type="tool_use", id="tu_1", name="lookup_property", input={"query": "maple court"}
    )
    final = DraftReply(reply_text="Maple Court 2B is $1650/mo.", property_id="P-101")
    responses = [
        SimpleNamespace(stop_reason="tool_use", content=[tool_call], parsed_output=None),
        SimpleNamespace(stop_reason="end_turn", content=[], parsed_output=final),
    ]
    llm = ClaudeLLM(client=_client(responses))
    inq = Inquiry.model_validate({"channel": "sms", "from": "+1", "body": "rent at maple court?"})
    cls = Classification(category=Category.PRICING, confidence=0.9, rationale="x")

    draft = llm.draft(inq, cls, ToolBox())

    assert draft.tools_used == ["lookup_property"]
    second_call = llm.client.messages.calls[1]
    tool_result = second_call["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "tu_1"
    assert '"P-101"' in tool_result["content"]
    assert second_call["tools"][0]["strict"] is True
