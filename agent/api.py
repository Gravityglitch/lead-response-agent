"""FastAPI surface. Webhooks in, pending queue + approve/reject out."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from agent.auth import require_api_key
from agent.config import Settings, get_settings
from agent.db import check_db, make_engine
from agent.models import Action, AgentResult, Inquiry
from agent.observability import RequestIdMiddleware, configure_logging
from agent.outbound import build_sender
from agent.pipeline import LeadAgent
from agent.queue import ApprovalQueue
from agent.tools import ToolBox
from agent.webhooks import router as webhook_router

log = structlog.get_logger(__name__)


def build_agent(settings: Settings) -> LeadAgent:
    toolbox = ToolBox()
    engine = make_engine(settings.database_url)
    queue = ApprovalQueue(toolbox, engine=engine, sender=build_sender(settings))
    return LeadAgent(toolbox=toolbox, queue=queue, settings=settings)


def create_app(agent: LeadAgent | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or (agent.settings if agent else get_settings())
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.agent = agent or build_agent(settings)
        log.info(
            "app.start",
            env=settings.app_env,
            llm=type(app.state.agent.llm).__name__,
            auth=settings.auth_enabled,
            outbound=settings.outbound_mode,
        )
        yield

    app = FastAPI(title="Lead Response Agent", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(RequestIdMiddleware)
    if settings.metrics_enabled:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(excluded_handlers=["/health", "/ready", "/metrics"]).instrument(app).expose(
            app, include_in_schema=False
        )

    protected = [Depends(require_api_key)]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "llm": type(app.state.agent.llm).__name__,
            "outbound": settings.outbound_mode,
        }

    @app.get("/ready")
    def ready() -> JSONResponse:
        ok = check_db(app.state.agent.queue.engine)
        return JSONResponse({"status": "ready" if ok else "degraded", "db": ok}, 200 if ok else 503)

    @app.post("/inbound", response_model=AgentResult, dependencies=protected)
    def inbound(
        inquiry: Inquiry, idempotency_key: str | None = Header(default=None)
    ) -> AgentResult:
        return app.state.agent.handle(inquiry, idempotency_key=idempotency_key)

    @app.get("/inquiries/{inquiry_id}", response_model=AgentResult, dependencies=protected)
    def get_inquiry(inquiry_id: str) -> AgentResult:
        result = app.state.agent.get_result(inquiry_id)
        if result is None:
            raise HTTPException(404, f"no inquiry {inquiry_id}")
        return result

    @app.post("/inquiries/{inquiry_id}/replay", response_model=AgentResult, dependencies=protected)
    def replay(inquiry_id: str) -> AgentResult:
        try:
            return app.state.agent.replay(inquiry_id)
        except KeyError as exc:
            raise HTTPException(404, f"no inquiry {inquiry_id}") from exc

    @app.get("/pending", response_model=list[Action], dependencies=protected)
    def pending() -> list[Action]:
        return app.state.agent.queue.list_pending()

    @app.post("/approve/{action_id}", response_model=Action, dependencies=protected)
    def approve(action_id: str) -> Action:
        try:
            return app.state.agent.queue.approve(action_id)
        except KeyError as exc:
            raise HTTPException(404, f"no pending action {action_id}") from exc

    @app.post("/reject/{action_id}", response_model=Action, dependencies=protected)
    def reject(action_id: str) -> Action:
        try:
            return app.state.agent.queue.reject(action_id)
        except KeyError as exc:
            raise HTTPException(404, f"no pending action {action_id}") from exc

    @app.get("/audit", dependencies=protected)
    def audit() -> list[dict]:
        return app.state.agent.queue.audit_log

    app.include_router(webhook_router)
    return app


app = create_app()
