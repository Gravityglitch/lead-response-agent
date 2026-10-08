"""LLM backends: a real Claude client and a deterministic fake for tests/evals.

Both expose the same two calls:
  classify(inquiry) -> Classification      (structured output, no tools)
  draft(inquiry, classification, toolbox) -> DraftReply  (tool loop + structured output)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol

from agent.models import Category, Classification, DraftReply, Inquiry
from agent.tools import TOOL_SCHEMAS, ToolBox

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOOL_ROUNDS = 6

CLASSIFY_SYSTEM = """You triage inbound inquiries for a residential property-management company.
Classify each message into exactly one category:
- availability: is a unit free, move-in dates, is it still listed
- pricing: rent, deposit, fees, pet rent, parking cost, what's included
- showing_request: wants to tour / view / visit / schedule a walkthrough
- maintenance: repairs, something broken, leaks, heat, pests, lockouts (current tenants)
- other: anything else - vendors, spam, legal, complaints, unrelated
If a message asks several things, pick the sender's primary intent.
Set property_hint to the unit name or address they mention, or null."""

DRAFT_SYSTEM = """You are a leasing assistant for Springfield Property Management.
Write a short, friendly reply (under 120 words) to the inquiry. Rules:
- Always call lookup_property first when a unit is mentioned; only state facts returned by tools.
- For showing requests, call propose_showing_slot and offer that slot as a *proposed* time;
  say a team member will confirm. Never claim it is booked.
- Call escalate for maintenance issues, legal/fair-housing topics, complaints, or anything
  the tool data cannot answer, and keep the reply to a brief acknowledgement.
- Never invent pricing, dates, or policies. If lookup_property finds nothing, ask which unit.
- Sign off as "Springfield PM Leasing Team"."""


class LLM(Protocol):
    def classify(self, inquiry: Inquiry) -> Classification: ...

    def draft(
        self, inquiry: Inquiry, classification: Classification, toolbox: ToolBox
    ) -> DraftReply: ...


def _inquiry_text(inquiry: Inquiry) -> str:
    subject = f"Subject: {inquiry.subject}\n" if inquiry.subject else ""
    return f"Channel: {inquiry.channel}\nFrom: {inquiry.sender}\n{subject}\n{inquiry.body}"


# --------------------------------------------------------------------------
# Claude
# --------------------------------------------------------------------------


class ClaudeLLM:
    def __init__(self, model: str | None = None, client: Any | None = None) -> None:
        import anthropic

        self.model = model or os.environ.get("LEAD_AGENT_MODEL", DEFAULT_MODEL)
        self.client = client or anthropic.Anthropic()

    def classify(self, inquiry: Inquiry) -> Classification:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=1024,
            system=CLASSIFY_SYSTEM,
            messages=[{"role": "user", "content": _inquiry_text(inquiry)}],
            output_format=Classification,
        )
        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(f"classification not parsed (stop_reason={response.stop_reason})")
        return parsed

    def draft(
        self, inquiry: Inquiry, classification: Classification, toolbox: ToolBox
    ) -> DraftReply:
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"{_inquiry_text(inquiry)}\n\n"
                    f"Pre-classified as: {classification.category} "
                    f"(property hint: {classification.property_hint or 'none'})"
                ),
            }
        ]
        tools_used: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=4096,
                system=DRAFT_SYSTEM,
                tools=TOOL_SCHEMAS,
                messages=messages,
                output_format=DraftReply,
            )
            if response.stop_reason != "tool_use":
                parsed = response.parsed_output
                if parsed is None:
                    raise RuntimeError(f"draft not parsed (stop_reason={response.stop_reason})")
                parsed.tools_used = sorted(set(tools_used))
                return parsed

            messages.append({"role": "assistant", "content": response.content})
            results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tools_used.append(block.name)
                try:
                    out = toolbox.call(block.name, dict(block.input))
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(out)}
                    )
                except Exception as exc:  # noqa: BLE001 - surface tool errors to the model
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error: {exc}",
                            "is_error": True,
                        }
                    )
            messages.append({"role": "user", "content": results})
        raise RuntimeError("tool loop exceeded MAX_TOOL_ROUNDS")


# --------------------------------------------------------------------------
# Deterministic fake (tests, offline eval, demos without an API key)
# --------------------------------------------------------------------------

_RULES: list[tuple[Category, tuple[str, ...]]] = [
    (
        Category.MAINTENANCE,
        (
            "leak",
            "broken",
            "repair",
            "not working",
            "no heat",
            "no hot water",
            "clog",
            "locked out",
            "mold",
            "pest",
            "roach",
            "ac ",
            "a/c",
            "furnace",
            "smoke detector",
            "fix ",
        ),
    ),
    (
        Category.SHOWING_REQUEST,
        (
            "tour",
            "showing",
            "view the",
            "visit",
            "walk through",
            "walkthrough",
            "see the unit",
            "see it",
            "come by",
            "stop by",
            "schedule",
            "appointment",
            "look at the",
        ),
    ),
    (
        Category.PRICING,
        (
            "rent",
            "price",
            "cost",
            "deposit",
            "fee",
            "how much",
            "$",
            "per month",
            "/mo",
            "utilities included",
            "pet rent",
            "application fee",
        ),
    ),
    (
        Category.AVAILABILITY,
        (
            "available",
            "availability",
            "still open",
            "still listed",
            "vacant",
            "move in",
            "move-in",
            "when can",
            "is it taken",
            "any units",
            "openings",
            "lease start",
        ),
    ),
]


class FakeLLM:
    """Keyword-rule stand-in for Claude. Deterministic, no network."""

    def __init__(self, property_names: list[str] | None = None) -> None:
        self.property_names = property_names or []

    def classify(self, inquiry: Inquiry) -> Classification:
        text = f"{inquiry.subject or ''} {inquiry.body}".lower()
        hint = next((n for n in self.property_names if n.split()[0].lower() in text), None)
        for category, keywords in _RULES:
            hits = [k for k in keywords if k in text]
            if hits:
                return Classification(
                    category=category,
                    confidence=min(0.95, 0.6 + 0.1 * len(hits)),
                    property_hint=hint,
                    rationale=f"matched keywords: {', '.join(hits[:3])}",
                )
        return Classification(
            category=Category.OTHER,
            confidence=0.5,
            property_hint=hint,
            rationale="no category keywords matched",
        )

    def draft(
        self, inquiry: Inquiry, classification: Classification, toolbox: ToolBox
    ) -> DraftReply:
        used: list[str] = []
        prop: dict[str, Any] | None = None
        if classification.property_hint:
            used.append("lookup_property")
            found = toolbox.call("lookup_property", {"query": classification.property_hint})
            prop = found["matches"][0] if found["found"] else None

        sign = "\n\nSpringfield PM Leasing Team"
        match classification.category:
            case Category.MAINTENANCE | Category.OTHER:
                used.append("escalate")
                reason = f"{classification.category} inquiry needs a human"
                toolbox.call("escalate", {"reason": reason})
                return DraftReply(
                    reply_text="Thanks for reaching out - a team member will follow up shortly."
                    + sign,
                    escalate=True,
                    escalation_reason=reason,
                    property_id=prop["id"] if prop else None,
                    tools_used=used,
                )
            case Category.SHOWING_REQUEST:
                if prop is None:
                    return DraftReply(
                        reply_text="Happy to set up a tour - which unit are you interested in?"
                        + sign,
                        tools_used=used,
                    )
                used.append("propose_showing_slot")
                slot = toolbox.call("propose_showing_slot", {"property_id": prop["id"]})
                if not slot["slot"]:
                    return DraftReply(
                        reply_text=f"We'd love to show you {prop['name']}. Our calendar is full "
                        "this week; a team member will reach out with times." + sign,
                        property_id=prop["id"],
                        tools_used=used,
                    )
                when = slot["slot"]["start"].replace("T", " at ")
                return DraftReply(
                    reply_text=f"We'd love to show you {prop['name']}. Would {when} "
                    f"({slot['timezone']}) work? A team member will confirm." + sign,
                    proposed_slot_id=slot["slot"]["id"],
                    property_id=prop["id"],
                    tools_used=used,
                )
            case Category.PRICING | Category.AVAILABILITY:
                if prop is None:
                    return DraftReply(
                        reply_text="Thanks for your interest! Which unit or address are you asking "
                        "about? I'll send details right over." + sign,
                        tools_used=used,
                    )
                facts = (
                    f"{prop['name']} is {prop['status'].replace('_', ' ')}, "
                    f"${prop['rent']}/month with a ${prop['deposit']} deposit, "
                    f"available from {prop['available_from']}. Pets: {prop['pets']}. "
                    f"Parking: {prop['parking']}."
                )
                return DraftReply(
                    reply_text=f"Thanks for reaching out! {facts} Want to set up a tour?" + sign,
                    property_id=prop["id"],
                    tools_used=used,
                )
        raise AssertionError("unreachable")  # pragma: no cover


def build_llm(toolbox: ToolBox) -> LLM:
    """Pick a backend from env. Falls back to the fake when no API key is configured."""
    names = [p["name"] for p in toolbox.crm["properties"]]
    choice = os.environ.get("LEAD_AGENT_LLM", "claude").lower()
    if choice == "fake":
        return FakeLLM(names)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY not set; using FakeLLM")
        return FakeLLM(names)
    return ClaudeLLM()
