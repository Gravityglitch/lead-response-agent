"""Read-only tools the agent can call, plus their JSON schemas for Claude."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ToolResult = dict[str, Any]


class ToolBox:
    """Local 'CRM' + calendar backed by JSON files. All methods here are read-only."""

    def __init__(self, crm_path: Path | None = None, calendar_path: Path | None = None) -> None:
        self.crm = json.loads((crm_path or DATA_DIR / "crm.json").read_text())
        self.calendar = json.loads((calendar_path or DATA_DIR / "calendar.json").read_text())
        self.escalations: list[dict[str, str]] = []

    # --- tools -------------------------------------------------------------

    def lookup_property(self, query: str) -> ToolResult:
        """Case-insensitive match on id, name, or address. Returns at most 3 listings."""
        q = query.lower().strip()
        tokens = [t for t in q.split() if len(t) > 2]
        scored: list[tuple[int, dict[str, Any]]] = []
        for prop in self.crm["properties"]:
            haystack = f"{prop['id']} {prop['name']} {prop['address']}".lower()
            score = sum(1 for t in tokens if t in haystack)
            if q and q in haystack:
                score += 5
            if score:
                scored.append((score, prop))
        scored.sort(key=lambda s: -s[0])
        matches = [p for _, p in scored[:3]]
        return {"query": query, "matches": matches, "found": bool(matches)}

    def propose_showing_slot(self, property_id: str) -> ToolResult:
        """Return the next open calendar slot."""
        open_slots = [s for s in self.calendar["slots"] if not s["booked"]]
        if not open_slots:
            return {"property_id": property_id, "slot": None}
        slot = open_slots[0]
        return {
            "property_id": property_id,
            "slot": slot,
            "timezone": self.calendar["timezone"],
            "alternatives": [s["id"] for s in open_slots[1:3]],
        }

    def escalate(self, reason: str) -> ToolResult:
        """Flag for a human. No side effect beyond recording the reason."""
        self.escalations.append({"reason": reason})
        return {"escalated": True, "reason": reason}

    # --- dispatch ----------------------------------------------------------

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        match name:
            case "lookup_property":
                return self.lookup_property(str(args["query"]))
            case "propose_showing_slot":
                return self.propose_showing_slot(str(args["property_id"]))
            case "escalate":
                return self.escalate(str(args["reason"]))
            case _:
                raise KeyError(f"unknown tool: {name}")


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "lookup_property",
        "description": (
            "Read-only CRM lookup. Search listings by id, name, street, or address fragment. "
            "Use this before answering any availability, pricing, pet, or parking question."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "propose_showing_slot",
        "description": (
            "Read the showing calendar and return the next open slot for a property. "
            "Does NOT book anything; booking requires human approval."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"property_id": {"type": "string"}},
            "required": ["property_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "escalate",
        "description": (
            "Hand the conversation to a human. Use for maintenance emergencies, legal or "
            "fair-housing questions, complaints, anything you cannot answer from tool data."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
]
