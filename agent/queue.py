"""Approval gate: every CRM write parks here until a human approves it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent.models import Action, ActionType
from agent.tools import ToolBox


class ApprovalQueue:
    """In-memory pending queue + audit log. Executes writes only on approve()."""

    def __init__(self, toolbox: ToolBox) -> None:
        self.toolbox = toolbox
        self.pending: dict[str, Action] = {}
        self.audit_log: list[dict[str, Any]] = []

    def submit(self, action: Action) -> Action:
        if action.requires_approval:
            self.pending[action.id] = action
            self._log("queued", action)
        else:
            self._execute(action)
        return action

    def approve(self, action_id: str) -> Action:
        action = self.pending.pop(action_id)
        action.status = "approved"
        self._log("approved", action)
        self._execute(action)
        return action

    def reject(self, action_id: str) -> Action:
        action = self.pending.pop(action_id)
        action.status = "rejected"
        self._log("rejected", action)
        return action

    def list_pending(self) -> list[Action]:
        return list(self.pending.values())

    # --- writes. These are the ONLY places CRM/calendar state is mutated. ---

    def _execute(self, action: Action) -> None:
        match action.type:
            case ActionType.UPDATE_LEAD:
                self.toolbox.crm["leads"].append(dict(action.payload))
            case ActionType.BOOK_SHOWING:
                slot_id = action.payload.get("slot_id")
                for slot in self.toolbox.calendar["slots"]:
                    if slot["id"] == slot_id:
                        slot["booked"] = True
                        slot["booked_for"] = action.payload.get("sender")
            case ActionType.ESCALATE:
                pass  # notification is the n8n workflow's job; we just log it
        action.status = "executed"
        self._log("executed", action)

    def _log(self, event: str, action: Action) -> None:
        self.audit_log.append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "event": event,
                "action_id": action.id,
                "type": action.type,
                "payload": action.payload,
            }
        )
