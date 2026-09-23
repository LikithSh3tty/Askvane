import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from src.llm.stub import StubLLM

REFERENCE = ["Whenever a new invoice arrives, notify my finance team.", "Gmail", "Finance",
             "Only above ₹10,000", "Slack", "#finance channel", "Yes"]


@pytest.fixture
def client():
    return TestClient(create_app(llm=StubLLM()))


def test_health_reports_provider(client):
    assert client.get("/health").json() == {"status": "ok", "provider": "stub"}


def test_full_conversation_over_http_reaches_a_workflow(client):
    session_id = None
    for message in REFERENCE:
        res = client.post("/chat", json={"session_id": session_id, "message": message})
        assert res.status_code == 200, res.text
        body = res.json()
        session_id = body["session_id"]
    assert body["complete"] is True
    assert body["workflow"]["nodes"][0]["type"] == "gmail_trigger"
    assert body["state_table"][-1] == {"parameter": "Status", "value": "All information collected"}


def test_state_table_updates_every_turn(client):
    first = client.post("/chat", json={"message": REFERENCE[0]}).json()
    second = client.post("/chat", json={"session_id": first["session_id"], "message": "Gmail"}).json()
    assert first["state_table"] != second["state_table"]
    assert second["workflow"] is None and second["complete"] is False


def test_get_session_returns_the_table(client):
    sid = client.post("/chat", json={"message": REFERENCE[0]}).json()["session_id"]
    view = client.get(f"/session/{sid}").json()
    assert view["session_id"] == sid
    assert [t["role"] for t in view["transcript"]] == ["user", "agent"]


def test_unknown_session_is_404_not_500(client):
    assert client.get("/session/nope").status_code == 404
    assert client.delete("/session/nope").status_code == 404
    assert client.post("/chat", json={"session_id": "nope", "message": "Gmail"}).status_code == 404


def test_delete_resets_the_session(client):
    sid = client.post("/chat", json={"message": REFERENCE[0]}).json()["session_id"]
    assert client.delete(f"/session/{sid}").status_code == 204
    assert client.get(f"/session/{sid}").status_code == 404


def test_unscripted_message_is_422_and_leaves_state_untouched(client):
    sid = client.post("/chat", json={"message": REFERENCE[0]}).json()["session_id"]
    res = client.post("/chat", json={"session_id": sid, "message": "something the stub never saw"})
    assert res.status_code == 422
    assert len(client.get(f"/session/{sid}").json()["transcript"]) == 2


def test_empty_message_is_rejected(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422


def test_index_page_is_served(client):
    res = client.get("/")
    assert res.status_code == 200 and "Collected information" in res.text
