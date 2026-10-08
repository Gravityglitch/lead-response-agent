"""FastAPI surface. Webhook in, pending queue + approve/reject out."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from agent.models import Action, AgentResult, Inquiry
from agent.pipeline import LeadAgent


def create_app(agent: LeadAgent | None = None) -> FastAPI:
    app = FastAPI(title="Lead Response Agent", version="0.1.0")
    app.state.agent = agent or LeadAgent()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "llm": type(app.state.agent.llm).__name__}

    @app.post("/inbound", response_model=AgentResult)
    def inbound(inquiry: Inquiry) -> AgentResult:
        return app.state.agent.handle(inquiry)

    @app.get("/pending", response_model=list[Action])
    def pending() -> list[Action]:
        return app.state.agent.queue.list_pending()

    @app.post("/approve/{action_id}", response_model=Action)
    def approve(action_id: str) -> Action:
        try:
            return app.state.agent.queue.approve(action_id)
        except KeyError as exc:
            raise HTTPException(404, f"no pending action {action_id}") from exc

    @app.post("/reject/{action_id}", response_model=Action)
    def reject(action_id: str) -> Action:
        try:
            return app.state.agent.queue.reject(action_id)
        except KeyError as exc:
            raise HTTPException(404, f"no pending action {action_id}") from exc

    @app.get("/audit")
    def audit() -> list[dict]:
        return app.state.agent.queue.audit_log

    return app


app = create_app()
