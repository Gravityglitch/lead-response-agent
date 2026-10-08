from agent.models import ActionType, Category, Inquiry
from agent.pipeline import LeadAgent


def _inq(body: str, channel: str = "sms") -> Inquiry:
    return Inquiry.model_validate({"channel": channel, "from": "+15550100000", "body": body})


def test_showing_request_proposes_slot_but_does_not_book(agent: LeadAgent) -> None:
    result = agent.handle(_inq("Can I tour Maple Court 2B on Saturday?"))
    assert result.classification.category == Category.SHOWING_REQUEST
    assert result.draft.proposed_slot_id == "S-1"
    assert "propose_showing_slot" in result.draft.tools_used
    types = {a.type for a in result.actions}
    assert types == {ActionType.UPDATE_LEAD, ActionType.BOOK_SHOWING}
    assert all(a.requires_approval for a in result.actions)
    # nothing written yet
    assert agent.toolbox.crm["leads"] == []
    assert agent.toolbox.calendar["slots"][0]["booked"] is False
    assert len(agent.queue.list_pending()) == 2


def test_pricing_answers_from_crm_only(agent: LeadAgent) -> None:
    result = agent.handle(_inq("How much is rent at Riverside Lofts?"))
    assert result.classification.category == Category.PRICING
    assert "$1350" in result.draft.reply_text
    assert result.draft.tools_used == ["lookup_property"]
    assert not result.escalated


def test_maintenance_is_escalated_and_escalation_executes_without_approval(
    agent: LeadAgent,
) -> None:
    result = agent.handle(_inq("The sink at Birch Townhome 4 is leaking"))
    assert result.classification.category == Category.MAINTENANCE
    assert result.escalated
    esc = next(a for a in result.actions if a.type == ActionType.ESCALATE)
    assert esc.requires_approval is False and esc.status == "executed"
    assert ActionType.BOOK_SHOWING not in {a.type for a in result.actions}


def test_approve_executes_write_and_reject_does_not(agent: LeadAgent) -> None:
    result = agent.handle(_inq("Can I visit Oak Street House tomorrow?"))
    book = next(a for a in result.actions if a.type == ActionType.BOOK_SHOWING)
    lead = next(a for a in result.actions if a.type == ActionType.UPDATE_LEAD)

    agent.queue.reject(lead.id)
    assert agent.toolbox.crm["leads"] == []

    agent.queue.approve(book.id)
    slot = next(s for s in agent.toolbox.calendar["slots"] if s["id"] == book.payload["slot_id"])
    assert slot["booked"] is True
    assert agent.queue.list_pending() == []
    events = [e["event"] for e in agent.queue.audit_log]
    assert events.count("executed") == 1 and "rejected" in events


def test_unknown_property_asks_for_clarification(agent: LeadAgent) -> None:
    result = agent.handle(_inq("What is the rent?"))
    assert result.draft.property_id is None
    assert "which unit" in result.draft.reply_text.lower()
