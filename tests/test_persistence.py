"""Approvals must survive a process restart: build a fresh app over the same database file."""

from pathlib import Path

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings
from agent.db import make_engine
from agent.llm import FakeLLM
from agent.pipeline import LeadAgent
from agent.queue import ApprovalQueue
from agent.tools import ToolBox
from tests.conftest import TEST_API_KEY


def _boot(db_path: Path, toolbox: ToolBox) -> TestClient:
    settings = Settings(
        _env_file=None, app_env="test", api_keys=[TEST_API_KEY], database_url=f"sqlite:///{db_path}"
    )
    queue = ApprovalQueue(toolbox, engine=make_engine(settings.database_url))
    llm = FakeLLM([p["name"] for p in toolbox.crm["properties"]])
    agent = LeadAgent(llm=llm, toolbox=toolbox, queue=queue, settings=settings)
    return TestClient(create_app(agent), headers={"X-API-Key": TEST_API_KEY})


def test_pending_actions_and_audit_survive_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    toolbox = ToolBox()

    with _boot(db_path, toolbox) as first:
        r = first.post(
            "/inbound",
            json={"channel": "sms", "from": "+15550100003", "body": "Tour Maple Court 2B?"},
        )
        assert r.status_code == 200
        inquiry_id = r.json()["inquiry_id"]
        pending_before = first.get("/pending").json()
        assert len(pending_before) == 3

    # "restart": new engine, new queue, new app over the same file
    with _boot(db_path, toolbox) as second:
        pending_after = second.get("/pending").json()
        assert {a["id"] for a in pending_after} == {a["id"] for a in pending_before}

        book = next(a for a in pending_after if a["type"] == "book_showing")
        assert second.post(f"/approve/{book['id']}").json()["status"] == "executed"
        assert second.get(f"/inquiries/{inquiry_id}").status_code == 200

        events = [e["event"] for e in second.get("/audit").json()]
        assert events.count("queued") == 3 and "approved" in events and "executed" in events

    with _boot(db_path, toolbox) as third:
        ids = {a["id"] for a in third.get("/pending").json()}
        assert book["id"] not in ids and len(ids) == 2
