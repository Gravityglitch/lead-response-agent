"""Pydantic models shared by the pipeline, API, and eval."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["email", "sms", "web_form"]


class Category(StrEnum):
    AVAILABILITY = "availability"
    PRICING = "pricing"
    SHOWING_REQUEST = "showing_request"
    MAINTENANCE = "maintenance"
    OTHER = "other"


class Inquiry(BaseModel):
    """Inbound message. Shape matches a typical n8n / Twilio / email-parse webhook."""

    channel: Channel
    sender: str = Field(alias="from", description="Email address or phone number")
    body: str = Field(min_length=1)
    subject: str | None = None
    provider_message_id: str | None = Field(
        default=None,
        description="Upstream message id (Twilio MessageSid, email Message-ID). Used to dedupe.",
    )
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = {"populate_by_name": True}


class Classification(BaseModel):
    """Structured output of the classification step."""

    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    property_hint: str | None = Field(
        default=None, description="Property name/address mentioned by the sender, if any"
    )
    rationale: str = Field(max_length=300)


class DraftReply(BaseModel):
    """Structured output of the reply-drafting step."""

    reply_text: str
    proposed_slot_id: str | None = Field(
        default=None, description="Calendar slot id offered to the sender, if any"
    )
    property_id: str | None = None
    escalate: bool = False
    escalation_reason: str | None = None
    tools_used: list[str] = Field(default_factory=list)


class ActionType(StrEnum):
    BOOK_SHOWING = "book_showing"
    UPDATE_LEAD = "update_lead"
    ESCALATE = "escalate"
    SEND_REPLY = "send_reply"


class Action(BaseModel):
    """A side effect the agent wants to perform. Writes require approval."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: ActionType
    payload: dict[str, str | int | float | bool | None]
    requires_approval: bool
    status: Literal["pending", "approved", "rejected", "executed", "failed"] = "pending"
    inquiry_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentResult(BaseModel):
    """What POST /inbound returns."""

    inquiry_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    inquiry: Inquiry
    classification: Classification
    draft: DraftReply
    actions: list[Action]
    escalated: bool
    duplicate: bool = Field(
        default=False, description="True when this inquiry was already processed (replayed result)"
    )
