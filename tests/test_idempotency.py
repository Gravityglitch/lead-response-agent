from fastapi.testclient import TestClient

from agent.pipeline import LeadAgent


def test_duplicate_provider_message_id_is_not_reprocessed(client: TestClient) -> None:
    body = {
        "channel": "sms",
        "from": "+15550100003",
        "body": "Tour Maple Court 2B?",
        "provider_message_id": "SM123",
    }
    first = client.post("/inbound", json=body).json()
    second = client.post("/inbound", json=body).json()
    assert first["duplicate"] is False and second["duplicate"] is True
    assert second["inquiry_id"] == first["inquiry_id"]
    assert [a["id"] for a in second["actions"]] == [a["id"] for a in first["actions"]]
    assert len(client.get("/pending").json()) == 3  # not 6


def test_idempotency_key_header_dedupes(client: TestClient) -> None:
    body = {"channel": "email", "from": "jo@example.com", "body": "How much is Cedar Studio 3?"}
    headers = {"Idempotency-Key": "abc"}
    a = client.post("/inbound", json=body, headers=headers).json()
    b = client.post("/inbound", json=body, headers=headers).json()
    assert b["duplicate"] and b["inquiry_id"] == a["inquiry_id"]
    c = client.post("/inbound", json=body).json()  # no key: processed again
    assert c["duplicate"] is False and c["inquiry_id"] != a["inquiry_id"]


def test_replay_reruns_pipeline_for_stored_inquiry(client: TestClient, agent: LeadAgent) -> None:
    body = {"channel": "sms", "from": "+1", "body": "Rent at Riverside Lofts?"}
    first = client.post("/inbound", json=body).json()
    r = client.post(f"/inquiries/{first['inquiry_id']}/replay")
    assert r.status_code == 200
    replayed = r.json()
    assert replayed["inquiry_id"] == first["inquiry_id"]
    assert {a["id"] for a in replayed["actions"]}.isdisjoint({a["id"] for a in first["actions"]})
    assert client.post("/inquiries/nope/replay").status_code == 404
    assert client.get("/inquiries/nope").status_code == 404
