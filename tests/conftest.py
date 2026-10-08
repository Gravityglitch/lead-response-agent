import pytest
from fastapi.testclient import TestClient

from agent.api import create_app
from agent.llm import FakeLLM
from agent.pipeline import LeadAgent
from agent.tools import ToolBox


@pytest.fixture
def toolbox() -> ToolBox:
    return ToolBox()


@pytest.fixture
def agent(toolbox: ToolBox) -> LeadAgent:
    names = [p["name"] for p in toolbox.crm["properties"]]
    return LeadAgent(llm=FakeLLM(names), toolbox=toolbox)


@pytest.fixture
def client(agent: LeadAgent) -> TestClient:
    return TestClient(create_app(agent))
