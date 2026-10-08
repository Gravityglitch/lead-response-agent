"""Approval gate: every write (CRM, calendar, outbound reply) parks here until approved.

State lives in the database so approvals survive restarts. This module is the only code that
mutates CRM/calendar state or sends a message.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agent.db import ActionRow, AuditRow, init_db, make_engine, make_session_factory
from agent.models import Action, ActionType
from agent.observability import current_request_id
from agent.outbound import DryRunSender, OutboundSender
from agent.tools import ToolBox

log = structlog.get_logger(__name__)


class ApprovalQueue:
    """Persistent pending queue + audit log. Executes writes only on approve()."""

    def __init__(
        self,
        toolbox: ToolBox,
        engine: Engine | None = None,
        sender: OutboundSender | None = None,
    ) -> None:
        self.toolbox = toolbox
        self.engine = engine or make_engine("sqlite://")
        init_db(self.engine)
        self._sessions = make_session_factory(self.engine)
        self.sender: OutboundSender = sender or DryRunSender()

    # --- queue operations --------------------------------------------------

    def submit(self, action: Action) -> Action:
        with self._sessions() as db:
            db.add(_to_row(action))
            db.commit()
            if action.requires_approval:
                self._log(db, "queued", action)
            else:
                self._execute(db, action)
            db.commit()
        return action

    def approve(self, action_id: str) -> Action:
        with self._sessions() as db:
            row = self._pending_row(db, action_id)
            row.status = "approved"
            action = _from_row(row)
            self._log(db, "approved", action)
            self._execute(db, action)
            db.commit()
        return action

    def reject(self, action_id: str) -> Action:
        with self._sessions() as db:
            row = self._pending_row(db, action_id)
            row.status = "rejected"
            action = _from_row(row)
            self._log(db, "rejected", action)
            db.commit()
        return action

    def list_pending(self) -> list[Action]:
        with self._sessions() as db:
            stmt = select(ActionRow).where(ActionRow.status == "pending")
            rows = db.scalars(stmt.order_by(ActionRow.created_at))
            return [_from_row(r) for r in rows]

    def get(self, action_id: str) -> Action | None:
        with self._sessions() as db:
            row = db.get(ActionRow, action_id)
            return _from_row(row) if row else None

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        with self._sessions() as db:
            rows = db.scalars(select(AuditRow).order_by(AuditRow.id))
            return [
                {
                    "ts": r.ts.isoformat(),
                    "event": r.event,
                    "action_id": r.action_id,
                    "type": r.action_type,
                    "payload": r.payload,
                    "request_id": r.request_id,
                }
                for r in rows
            ]

    # --- writes. These are the ONLY places CRM/calendar/outbound state is mutated. ---

    def _execute(self, db: Session, action: Action) -> None:
        row = db.get(ActionRow, action.id)
        assert row is not None
        try:
            result = self._perform(action)
        except Exception as exc:  # noqa: BLE001 - record failure, keep the row for replay
            row.status = action.status = "failed"
            self._log(db, "failed", action, {"error": str(exc)})
            log.error("action.failed", action_id=action.id, type=action.type, error=str(exc))
            return
        row.status = action.status = "executed"
        self._log(db, "executed", action, result)

    def _perform(self, action: Action) -> dict[str, Any]:
        match action.type:
            case ActionType.UPDATE_LEAD:
                self.toolbox.crm["leads"].append(dict(action.payload))
                return {}
            case ActionType.BOOK_SHOWING:
                slot_id = action.payload.get("slot_id")
                for slot in self.toolbox.calendar["slots"]:
                    if slot["id"] == slot_id:
                        slot["booked"] = True
                        slot["booked_for"] = action.payload.get("sender")
                return {"slot_id": slot_id}
            case ActionType.SEND_REPLY:
                return self.sender.send(
                    channel=action.payload["channel"],  # type: ignore[arg-type]
                    to=str(action.payload["to"]),
                    body=str(action.payload["body"]),
                    subject=_opt_str(action.payload.get("subject")),
                )
            case ActionType.ESCALATE:
                return {}  # notification is the n8n workflow's job; we just log it
        raise AssertionError("unreachable")  # pragma: no cover

    def _pending_row(self, db: Session, action_id: str) -> ActionRow:
        row = db.get(ActionRow, action_id)
        if row is None or row.status != "pending":
            raise KeyError(action_id)
        return row

    def _log(
        self, db: Session, event: str, action: Action, extra: dict[str, Any] | None = None
    ) -> None:
        payload: dict[str, Any] = dict(action.payload)
        if extra:
            payload["result"] = extra
        db.add(
            AuditRow(
                event=event,
                action_id=action.id,
                action_type=str(action.type),
                payload=payload,
                request_id=current_request_id(),
            )
        )
        log.info("action." + event, action_id=action.id, type=str(action.type))


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _to_row(action: Action) -> ActionRow:
    return ActionRow(
        id=action.id,
        type=str(action.type),
        status=action.status,
        requires_approval=action.requires_approval,
        payload=dict(action.payload),
        inquiry_id=action.inquiry_id,
        created_at=action.created_at,
    )


def _from_row(row: ActionRow) -> Action:
    return Action(
        id=row.id,
        type=ActionType(row.type),
        status=row.status,  # type: ignore[arg-type]
        requires_approval=row.requires_approval,
        payload=dict(row.payload),
        inquiry_id=row.inquiry_id,
        created_at=row.created_at,
    )
