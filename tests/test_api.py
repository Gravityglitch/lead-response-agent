from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["llm"] == "FakeLLM"


def test_inbound_pending_approve_flow(client: TestClient) -> None:
    r = client.post(
        "/inbound",
        json={"channel": "web_form", "from": "jo@example.com", "body": "Tour Cedar Studio 3?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["classification"]["category"] == "showing_request"
    assert body["escalated"] is False

    pending = client.get("/pending").json()
    assert {a["type"] for a in pending} == {"update_lead", "book_showing"}

    book = next(a for a in pending if a["type"] == "book_showing")
    r = client.post(f"/approve/{book['id']}")
    assert r.status_code == 200 and r.json()["status"] == "executed"

    lead = next(a for a in pending if a["type"] == "update_lead")
    r = client.post(f"/reject/{lead['id']}")
    assert r.status_code == 200 and r.json()["status"] == "rejected"

    assert client.get("/pending").json() == []
    assert client.post(f"/approve/{book['id']}").status_code == 404


def test_inbound_rejects_bad_payload(client: TestClient) -> None:
    assert client.post("/inbound", json={"channel": "sms"}).status_code == 422
