"""Orchestration: classify -> draft (tools) -> propose actions -> gate writes."""

from __future__ import annotations

from agent.llm import LLM, build_llm
from agent.models import Action, ActionType, AgentResult, Category, Inquiry
from agent.queue import ApprovalQueue
from agent.tools import ToolBox

CONFIDENCE_FLOOR = 0.6
ALWAYS_ESCALATE = {Category.MAINTENANCE, Category.OTHER}


class LeadAgent:
    def __init__(
        self,
        llm: LLM | None = None,
        toolbox: ToolBox | None = None,
        queue: ApprovalQueue | None = None,
    ) -> None:
        self.toolbox = toolbox or ToolBox()
        self.llm = llm or build_llm(self.toolbox)
        self.queue = queue or ApprovalQueue(self.toolbox)

    def handle(self, inquiry: Inquiry) -> AgentResult:
        classification = self.llm.classify(inquiry)
        draft = self.llm.draft(inquiry, classification, self.toolbox)

        escalated = (
            draft.escalate
            or classification.category in ALWAYS_ESCALATE
            or classification.confidence < CONFIDENCE_FLOOR
        )
        if escalated and not draft.escalation_reason:
            draft.escalate = True
            draft.escalation_reason = (
                f"policy: category={classification.category}, "
                f"confidence={classification.confidence:.2f}"
            )

        actions: list[Action] = [
            Action(
                type=ActionType.UPDATE_LEAD,
                requires_approval=True,
                payload={
                    "sender": inquiry.sender,
                    "channel": inquiry.channel,
                    "category": classification.category,
                    "property_id": draft.property_id,
                    "summary": classification.rationale,
                    "draft_reply": draft.reply_text,
                },
            )
        ]
        if draft.proposed_slot_id and not escalated:
            actions.append(
                Action(
                    type=ActionType.BOOK_SHOWING,
                    requires_approval=True,
                    payload={
                        "sender": inquiry.sender,
                        "property_id": draft.property_id,
                        "slot_id": draft.proposed_slot_id,
                    },
                )
            )
        if escalated:
            actions.append(
                Action(
                    type=ActionType.ESCALATE,
                    requires_approval=False,
                    payload={"sender": inquiry.sender, "reason": draft.escalation_reason},
                )
            )

        for action in actions:
            self.queue.submit(action)

        return AgentResult(
            inquiry=inquiry,
            classification=classification,
            draft=draft,
            actions=actions,
            escalated=escalated,
        )
