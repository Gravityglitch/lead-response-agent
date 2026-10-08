"""Retry path and model-failure escalation, with a scripted Anthropic stand-in."""

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from agent.llm import ClaudeLLM, LLMError
from agent.models import Category, Classification, Inquiry
from agent.pipeline import LeadAgent
from agent.tools import ToolBox

INQ = Inquiry.model_validate({"channel": "sms", "from": "+1", "body": "rent?"})


class FlakyMessages:
    def __init__(self, failures: list[Exception], final: object) -> None:
        self.failures = failures
        self.final = final
        self.calls = 0

    def parse(self, **kwargs: object) -> object:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.final


def _status_error(code: int) -> anthropic.APIStatusError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(code, request=req)
    return anthropic.APIStatusError("boom", response=resp, body=None)


def _llm(messages: FlakyMessages, retries: int = 3) -> ClaudeLLM:
    llm = ClaudeLLM(client=SimpleNamespace(messages=messages), max_retries=retries)
    llm._parse.retry.wait = lambda *_: 0  # type: ignore[attr-defined]
    return llm


def _ok() -> SimpleNamespace:
    cls = Classification(category=Category.PRICING, confidence=0.9, rationale="x")
    usage = SimpleNamespace(input_tokens=120, output_tokens=30)
    return SimpleNamespace(stop_reason="end_turn", parsed_output=cls, usage=usage)


def test_retries_transient_errors_then_succeeds() -> None:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    messages = FlakyMessages([anthropic.APIConnectionError(request=req), _status_error(503)], _ok())
    llm = _llm(messages)
    assert llm.classify(INQ).category == Category.PRICING
    assert messages.calls == 3


def test_does_not_retry_client_errors() -> None:
    messages = FlakyMessages([_status_error(400)], _ok())
    with pytest.raises(anthropic.APIStatusError):
        _llm(messages).classify(INQ)
    assert messages.calls == 1


def test_gives_up_after_max_retries() -> None:
    messages = FlakyMessages([_status_error(500)] * 5, _ok())
    with pytest.raises(anthropic.APIStatusError):
        _llm(messages, retries=2).classify(INQ)
    assert messages.calls == 2


def test_unparsed_output_raises_llm_error() -> None:
    messages = FlakyMessages([], SimpleNamespace(stop_reason="max_tokens", parsed_output=None))
    with pytest.raises(LLMError):
        _llm(messages).classify(INQ)


class BrokenLLM:
    def classify(self, inquiry: Inquiry) -> Classification:
        raise _status_error(500)

    def draft(self, *args: object) -> None:  # pragma: no cover - never reached
        raise AssertionError


def test_model_failure_escalates_and_never_drops(agent: LeadAgent) -> None:
    agent.llm = BrokenLLM()
    result = agent.handle(INQ)
    assert result.escalated is True
    assert result.classification.confidence == 0.0
    assert "model failure" in (result.draft.escalation_reason or "")
    types = [a.type for a in result.actions]
    assert "escalate" in types and "send_reply" in types and "book_showing" not in types
    reply = next(a for a in result.actions if a.type == "send_reply")
    assert reply.requires_approval is True
    assert ToolBox().crm["leads"] == []
