"""SQLAlchemy 2.x persistence: pending actions, audit log, processed inquiries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, Text, create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ActionRow(Base):
    """One proposed side effect. Status transitions: pending -> approved/rejected -> executed."""

    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    requires_approval: Mapped[bool] = mapped_column(default=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    inquiry_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class AuditRow(Base):
    """Append-only event log for every queue/approve/reject/execute transition."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    event: Mapped[str] = mapped_column(String(32))
    action_id: Mapped[str] = mapped_column(String(32), index=True)
    action_type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class InquiryRow(Base):
    """Every handled inquiry plus its result, keyed by the provider message id for dedupe."""

    __tablename__ = "inquiries"
    __table_args__ = (Index("ix_inquiries_dedupe_key", "dedupe_key", unique=True),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel: Mapped[str] = mapped_column(String(16))
    sender: Mapped[str] = mapped_column(String(255), index=True)
    body: Mapped[str] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="processed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


def make_engine(database_url: str) -> Engine:
    """Create an engine. In-memory SQLite shares one connection so state survives sessions."""
    if database_url.startswith("sqlite"):
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in database_url or database_url.endswith("sqlite://"):
            kwargs["poolclass"] = StaticPool
        return create_engine(database_url, **kwargs)
    return create_engine(database_url, pool_pre_ping=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def check_db(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - readiness probe must never raise
        return False


__all__ = [
    "ActionRow",
    "AuditRow",
    "Base",
    "InquiryRow",
    "check_db",
    "init_db",
    "make_engine",
    "make_session_factory",
    "select",
]
