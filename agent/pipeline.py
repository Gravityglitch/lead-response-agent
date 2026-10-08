"""Orchestration: classify -> draft (tools) -> propose actions -> gate writes.

Also owns idempotency (dedupe by provider message id) and the model-failure path: when the
LLM is unavailable the inquiry is escalated with a holding reply, never dropped.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select

from agent.config import Settings
from agent.db import InquiryRow
from agent.llm import LLM, build_llm
from agent.models import (
    Action,
    ActionType,
    AgentResult,
    Category,
    Classification,
    DraftReply,
    Inquiry,
)
from agent.queue import ApprovalQueue
from agent.tools import ToolBox

log = structlog.get_logger(__name__)

CONFIDENCE_FLOOR = 0.6
ALWAYS_ESCALATE = {Category.MAINTENANCE, Category.OTHER}
FALLBACK_REPLY = (
    "Thanks for reaching out - a member of our team will follow up with you shortly."
    "\n\nSpringfield PM Leasing Team"
)


class LeadAgent:
    def __init__(
        self,
        llm: LLM | None = None,
        toolbox: ToolBox | None = None,
        queue: ApprovalQueue | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.toolbox = toolbox or ToolBox()
        self.llm = llm or build_llm(self.toolbox, self.settings)
        self.queue = queue or ApprovalQueue(self.toolbox)
        self._sessions = self.queue._sessions
        self.auto_send = set(self.settings.auto_send_categories)

    # --- public ------------------------------------------------------------

    def handle(self, inquiry: Inquiry, idempotency_key: str | None = None) -> AgentResult:
        key = inquiry.provider_message_id or idempotency_key
        if key:
            existing = self._find_processed(key)
            if existing is not None:
                log.info("inquiry.duplicate", dedupe_key=key, inquiry_id=existing.inquiry_id)
                existing.duplicate = True
                return existing
        result = self._run(inquiry)
        self._store(result, key)
        return result

    def replay(self, inquiry_id: str) -> AgentResult:
        """Re-run the pipeline for a stored inquiry (new actions, new audit entries)."""
        with self._sessions() as db:
            row = db.get(InquiryRow, inquiry_id)
            if row is None:
                raise KeyError(inquiry_id)
            inquiry = Inquiry.model_validate(row.result["inquiry"])
        result = self._run(inquiry)
        with self._sessions() as db:
            row = db.get(InquiryRow, inquiry_id)
            assert row is not None
            row.result = result.model_dump(mode="json", by_alias=True)
            row.status = "replayed"
            db.commit()
        result.inquiry_id = inquiry_id
        return result

    def get_result(self, inquiry_id: str) -> AgentResult | None:
        with self._sessions() as db:
            row = db.get(InquiryRow, inquiry_id)
            return AgentResult.model_validate(row.result) if row else None

    # --- pipeline ----------------------------------------------------------

    def _run(self, inquiry: Inquiry) -> AgentResult:
        inquiry_id = uuid.uuid4().hex[:12]
        structlog.contextvars.bind_contextvars(inquiry_id=inquiry_id)
        try:
            classification = self.llm.classify(inquiry)
            draft = self.llm.draft(inquiry, classification, self.toolbox)
            model_failed = False
        except Exception as exc:  # noqa: BLE001 - escalate on any model failure, never drop
            log.error("llm.failed", error=str(exc), error_type=type(exc).__name__)
            classification = Classification(
                category=Category.OTHER,
                confidence=0.0,
                rationale=f"model failure: {type(exc).__name__}",
            )
            draft = DraftReply(
                reply_text=FALLBACK_REPLY,
                escalate=True,
                escalation_reason=f"model failure: {exc}"[:300],
            )
            model_failed = True

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

        actions = self._build_actions(inquiry, inquiry_id, classification, draft, escalated)
        for action in actions:
            self.queue.submit(action)

        log.info(
            "inquiry.handled",
            category=str(classification.category),
            confidence=classification.confidence,
            escalated=escalated,
            model_failed=model_failed,
            actions=[str(a.type) for a in actions],
        )
        return AgentResult(
            inquiry_id=inquiry_id,
            inquiry=inquiry,
            classification=classification,
            draft=draft,
            actions=actions,
            escalated=escalated,
        )

    def _build_actions(
        self,
        inquiry: Inquiry,
        inquiry_id: str,
        classification: Classification,
        draft: DraftReply,
        escalated: bool,
    ) -> list[Action]:
        actions: list[Action] = [
            Action(
                type=ActionType.UPDATE_LEAD,
                requires_approval=True,
                inquiry_id=inquiry_id,
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
                    inquiry_id=inquiry_id,
                    payload={
                        "sender": inquiry.sender,
                        "property_id": draft.property_id,
                        "slot_id": draft.proposed_slot_id,
                    },
                )
            )
        auto_send = not escalated and classification.category in self.auto_send
        actions.append(
            Action(
                type=ActionType.SEND_REPLY,
                requires_approval=not auto_send,
                inquiry_id=inquiry_id,
                payload={
                    "channel": inquiry.channel,
                    "to": inquiry.sender,
                    "subject": f"Re: {inquiry.subject}" if inquiry.subject else None,
                    "body": draft.reply_text,
                    "category": classification.category,
                },
            )
        )
        if escalated:
            actions.append(
                Action(
                    type=ActionType.ESCALATE,
                    requires_approval=False,
                    inquiry_id=inquiry_id,
                    payload={"sender": inquiry.sender, "reason": draft.escalation_reason},
                )
            )
        return actions

    # --- persistence -------------------------------------------------------

    def _find_processed(self, key: str) -> AgentResult | None:
        with self._sessions() as db:
            row = db.scalar(select(InquiryRow).where(InquiryRow.dedupe_key == key))
            return AgentResult.model_validate(row.result) if row else None

    def _store(self, result: AgentResult, key: str | None) -> None:
        with self._sessions() as db:
            db.add(
                InquiryRow(
                    id=result.inquiry_id,
                    dedupe_key=key,
                    channel=result.inquiry.channel,
                    sender=result.inquiry.sender,
                    body=result.inquiry.body,
                    result=result.model_dump(mode="json", by_alias=True),
                )
            )
            db.commit()
