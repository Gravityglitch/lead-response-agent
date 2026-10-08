"""send_reply sits behind the approval gate; adapters are exercised against a fake transport."""

import httpx

from agent.config import Settings
from agent.llm import FakeLLM
from agent.models import ActionType, Inquiry
from agent.outbound import ChannelRouter, DryRunSender, SendGridSender, TwilioSender, build_sender
from agent.pipeline import LeadAgent
from agent.queue import ApprovalQueue
from agent.tools import ToolBox


def _inq(body: str, channel: str = "sms") -> Inquiry:
    return Inquiry.model_validate({"channel": channel, "from": "+15550100000", "body": body})


def test_send_reply_waits_for_approval_then_sends(agent: LeadAgent) -> None:
    result = agent.handle(_inq("How much is rent at Riverside Lofts?"))
    reply = next(a for a in result.actions if a.type == ActionType.SEND_REPLY)
    assert reply.requires_approval and reply.status == "pending"
    sender = agent.queue.sender
    assert isinstance(sender, DryRunSender) and sender.sent == []

    agent.queue.approve(reply.id)
    assert len(sender.sent) == 1
    assert sender.sent[0]["to"] == "+15550100000" and "$1350" in sender.sent[0]["body"]
    assert sender.sent[0]["channel"] == "sms"


def test_rejected_reply_is_never_sent(agent: LeadAgent) -> None:
    result = agent.handle(_inq("How much is rent at Riverside Lofts?"))
    reply = next(a for a in result.actions if a.type == ActionType.SEND_REPLY)
    agent.queue.reject(reply.id)
    assert agent.queue.sender.sent == []


def test_auto_send_allowlist_skips_approval_but_not_when_escalated(
    toolbox: ToolBox, fake_llm: FakeLLM
) -> None:
    settings = Settings(
        _env_file=None, app_env="test", database_url="sqlite://", auto_send_categories="pricing"
    )
    agent = LeadAgent(llm=fake_llm, toolbox=toolbox, settings=settings)
    result = agent.handle(_inq("How much is rent at Riverside Lofts?"))
    reply = next(a for a in result.actions if a.type == ActionType.SEND_REPLY)
    assert reply.requires_approval is False and reply.status == "executed"
    assert len(agent.queue.sender.sent) == 1

    # escalated threads are never auto-sent, even if the category is allowlisted
    agent.auto_send = {"maintenance"}
    result = agent.handle(_inq("The sink at Birch Townhome 4 is leaking"))
    reply = next(a for a in result.actions if a.type == ActionType.SEND_REPLY)
    assert reply.requires_approval is True
    assert len(agent.queue.sender.sent) == 1


def test_failed_send_is_recorded_not_lost(toolbox: ToolBox, fake_llm: FakeLLM) -> None:
    class Boom:
        def send(self, channel, to, body, subject=None):  # noqa: ANN001
            raise RuntimeError("provider down")

    queue = ApprovalQueue(toolbox, sender=Boom())
    agent = LeadAgent(llm=fake_llm, toolbox=toolbox, queue=queue)
    result = agent.handle(_inq("How much is rent at Riverside Lofts?"))
    reply = next(a for a in result.actions if a.type == ActionType.SEND_REPLY)
    out = queue.approve(reply.id)
    assert out.status == "failed"
    failed = [e for e in queue.audit_log if e["event"] == "failed"]
    assert failed and "provider down" in failed[0]["payload"]["result"]["error"]


def test_twilio_and_sendgrid_adapters_build_correct_requests() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "twilio" in request.url.host:
            return httpx.Response(201, json={"sid": "SM9", "status": "queued"})
        return httpx.Response(202, headers={"X-Message-Id": "mid-1"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    router = ChannelRouter(
        sms=TwilioSender("AC1", "tok", "+15550000000", client=http),
        email=SendGridSender("SG.key", "leasing@example.com", client=http),
    )
    sms = router.send("sms", "+15551111111", "hi")
    email = router.send("web_form", "jo@example.com", "hello", subject="Re: tour")
    assert sms == {"provider": "twilio", "message_id": "SM9", "status": "queued"}
    assert email["provider"] == "sendgrid" and email["message_id"] == "mid-1"
    assert "Accounts/AC1/Messages.json" in str(seen[0].url)
    assert b"To=%2B15551111111" in seen[0].content
    assert seen[1].headers["Authorization"] == "Bearer SG.key"
    assert b'"subject":"Re: tour"' in seen[1].content


def test_build_sender_defaults_to_dry_run() -> None:
    assert isinstance(
        build_sender(Settings(_env_file=None, database_url="sqlite://")), DryRunSender
    )
    live = Settings(
        _env_file=None,
        database_url="sqlite://",
        outbound_mode="live",
        twilio_account_sid="AC",
        twilio_auth_token="t",
        twilio_from_number="+1",
        sendgrid_api_key="k",
        sendgrid_from_email="a@b.c",
    )
    assert isinstance(build_sender(live), ChannelRouter)
