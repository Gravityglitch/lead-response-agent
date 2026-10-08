from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings
from agent.pipeline import LeadAgent


def test_health_and_ready_are_public(client: TestClient) -> None:
    client.headers.pop("X-API-Key")
    assert client.get("/health").status_code == 200
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["db"] is True


def test_protected_routes_reject_missing_or_wrong_key(client: TestClient) -> None:
    client.headers.pop("X-API-Key")
    assert client.get("/pending").status_code == 401
    assert client.get("/audit").status_code == 401
    body = {"channel": "sms", "from": "+1", "body": "rent?"}
    assert client.post("/inbound", json=body).status_code == 401
    assert client.post("/inbound", json=body, headers={"X-API-Key": "nope"}).status_code == 401
    assert client.post("/approve/x", headers={"X-API-Key": "nope"}).status_code == 401


def test_valid_key_passes(client: TestClient) -> None:
    assert client.get("/pending").status_code == 200


def test_auth_disabled_when_no_keys_configured(agent: LeadAgent) -> None:
    settings = Settings(
        _env_file=None, app_env="development", api_keys=[], database_url="sqlite://"
    )
    agent.settings = settings
    with TestClient(create_app(agent, settings)) as c:
        assert c.get("/pending").status_code == 200


def test_production_settings_require_keys_and_postgres() -> None:
    import pytest

    with pytest.raises(ValueError, match="API_KEYS"):
        Settings(_env_file=None, app_env="production", database_url="sqlite://")
    with pytest.raises(ValueError, match="Postgres"):
        Settings(_env_file=None, app_env="production", api_keys=["k"], database_url="sqlite://")


def test_csv_settings_parse() -> None:
    s = Settings(_env_file=None, api_keys="a, b", auto_send_categories="pricing,availability")
    assert s.api_keys == ["a", "b"]
    assert [c.value for c in s.auto_send_categories] == ["pricing", "availability"]


def test_csv_env_vars_parse(monkeypatch) -> None:
    """API_KEYS and AUTO_SEND_CATEGORIES are plain CSV in the environment, not JSON."""
    monkeypatch.setenv("API_KEYS", "k1, k2")
    monkeypatch.setenv("AUTO_SEND_CATEGORIES", "pricing,availability")
    s = Settings(_env_file=None)
    assert s.api_keys == ["k1", "k2"]
    assert s.auto_send_categories == ["pricing", "availability"]
