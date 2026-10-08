from fastapi.testclient import TestClient

from agent.api import create_app
from agent.config import Settings
from agent.pipeline import LeadAgent
from agent.webhooks import twilio_signature, verify_twilio

TOKEN = "12345"


def test_twilio_signature_matches_documented_example() -> None:
    # Example from https://www.twilio.com/docs/usage/webhooks/webhooks-security
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+12349013030",
        "Digits": "1234",
        "From": "+12349013030",
        "To": "+18005551212",
    }
    sig = twilio_signature(TOKEN, url, params)
    assert sig == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="
    assert verify_twilio(TOKEN, url, params, sig)
    assert not verify_twilio(TOKEN, url, {**params, "Digits": "9"}, sig)


def _client(agent: LeadAgent, **overrides: object) -> TestClient:
    kwargs: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "api_keys": ["k"],
        "database_url": "sqlite://",
        "twilio_webhook_enabled": True,
        "twilio_auth_token": TOKEN,
        "twilio_webhook_url": "https://agent.example.com/webhooks/twilio",
        "sendgrid_webhook_enabled": True,
        "sendgrid_inbound_secret": "s3cret",
    }
    settings = Settings(**{**kwargs, **overrides})  # type: ignore[arg-type]
    agent.settings = settings
    return TestClient(create_app(agent, settings))


def test_twilio_webhook_accepts_signed_and_rejects_unsigned(agent: LeadAgent) -> None:
    form = {"From": "+15550100003", "Body": "Can I tour Maple Court 2B?", "MessageSid": "SM1"}
    url = "https://agent.example.com/webhooks/twilio"
    with _client(agent) as c:
        assert c.post("/webhooks/twilio", data=form).status_code == 403
        bad = {"X-Twilio-Signature": "nope"}
        assert c.post("/webhooks/twilio", data=form, headers=bad).status_code == 403
        good = {"X-Twilio-Signature": twilio_signature(TOKEN, url, form)}
        r = c.post("/webhooks/twilio", data=form, headers=good)
        assert r.status_code == 200
        assert r.json()["classification"]["category"] == "showing_request"
        assert r.json()["inquiry"]["provider_message_id"] == "SM1"
        # replay of the same MessageSid is a dedupe hit
        assert c.post("/webhooks/twilio", data=form, headers=good).json()["duplicate"] is True


def test_twilio_webhook_disabled_by_flag(agent: LeadAgent) -> None:
    with _client(agent, twilio_webhook_enabled=False) as c:
        assert c.post("/webhooks/twilio", data={"Body": "x"}).status_code == 404


def test_sendgrid_webhook_requires_token_and_parses_message_id(agent: LeadAgent) -> None:
    form = {
        "from": "jo@example.com",
        "subject": "Rent?",
        "text": "How much is Cedar Studio 3?",
        "headers": "Received: x\nMessage-ID: <abc@mail.example.com>\n",
    }
    with _client(agent) as c:
        assert c.post("/webhooks/sendgrid", data=form).status_code == 403
        assert c.post("/webhooks/sendgrid?token=wrong", data=form).status_code == 403
        r = c.post("/webhooks/sendgrid?token=s3cret", data=form)
        assert r.status_code == 200
        body = r.json()
        assert body["classification"]["category"] == "pricing"
        assert body["inquiry"]["provider_message_id"] == "<abc@mail.example.com>"
        assert body["inquiry"]["subject"] == "Rent?"
