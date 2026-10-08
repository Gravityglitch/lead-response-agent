import os

import pytest
from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings
from agent.llm import FakeLLM
from agent.pipeline import LeadAgent
from agent.tools import ToolBox

TEST_API_KEY = "test-key"

# Make sure a developer's real .env never leaks into the test run.
os.environ.pop("ANTHROPIC_API_KEY", None)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        api_keys=[TEST_API_KEY],
        database_url="sqlite://",
        lead_agent_llm="fake",
        log_json=False,
    )


@pytest.fixture
def toolbox() -> ToolBox:
    return ToolBox()


@pytest.fixture
def fake_llm(toolbox: ToolBox) -> FakeLLM:
    return FakeLLM([p["name"] for p in toolbox.crm["properties"]])


@pytest.fixture
def agent(toolbox: ToolBox, fake_llm: FakeLLM, settings: Settings) -> LeadAgent:
    return LeadAgent(llm=fake_llm, toolbox=toolbox, settings=settings)


@pytest.fixture
def client(agent: LeadAgent) -> TestClient:
    with TestClient(create_app(agent), headers={"X-API-Key": TEST_API_KEY}) as c:
        yield c
