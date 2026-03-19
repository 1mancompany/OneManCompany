import pytest
import yaml
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI


@pytest.fixture
def conversation_service(tmp_path, monkeypatch):
    """Create a ConversationService with tmp_path isolation."""
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    from onemancompany.core.conversation import ConversationService
    return ConversationService()


@pytest.fixture
def test_app(conversation_service, monkeypatch):
    """Patch _conversation_service in routes and return a FastAPI test app."""
    import onemancompany.api.routes as routes_mod
    monkeypatch.setattr(routes_mod, "_conversation_service", conversation_service)
    app = FastAPI()
    app.include_router(routes_mod.router)
    return app


@pytest.fixture
async def client(test_app):
    """Async HTTP client for the test app."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_conversation_api(client):
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone",
        "employee_id": "00100",
        "tools_enabled": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["phase"] == "active"


@pytest.mark.asyncio
async def test_send_message_api(client):
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
    })
    conv_id = resp.json()["id"]

    resp = await client.post(f"/api/conversation/{conv_id}/message", json={
        "text": "hello",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


@pytest.mark.asyncio
async def test_get_messages_api(client):
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
    })
    conv_id = resp.json()["id"]

    await client.post(f"/api/conversation/{conv_id}/message", json={"text": "hi"})

    resp = await client.get(f"/api/conversation/{conv_id}/messages")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert len(msgs) >= 1
    assert msgs[0]["text"] == "hi"


@pytest.mark.asyncio
async def test_close_conversation_api(client):
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
    })
    conv_id = resp.json()["id"]

    resp = await client.post(f"/api/conversation/{conv_id}/close")
    assert resp.status_code == 200
    assert resp.json()["phase"] == "closed"


@pytest.mark.asyncio
async def test_list_conversations_api(client):
    await client.post("/api/conversation/create", json={
        "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
    })

    resp = await client.get("/api/conversations")
    assert resp.status_code == 200
    convs = resp.json()["conversations"]
    assert len(convs) == 1


@pytest.mark.asyncio
async def test_create_oneonone_reuses_existing_history(client):
    # Create first thread and store a message
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone",
        "employee_id": "00100",
        "tools_enabled": True,
    })
    conv_id = resp.json()["id"]

    resp = await client.post(f"/api/conversation/{conv_id}/message", json={"text": "first check-in"})
    assert resp.status_code == 200

    # End meeting, then restart one-on-one
    resp = await client.post(f"/api/conversation/{conv_id}/close")
    assert resp.status_code == 200
    assert resp.json()["phase"] == "closed"

    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone",
        "employee_id": "00100",
        "tools_enabled": True,
    })
    assert resp.status_code == 200
    restarted = resp.json()

    # Should reopen existing thread (same id) with preserved history
    assert restarted["id"] == conv_id
    assert restarted["phase"] == "active"

    resp = await client.get(f"/api/conversation/{conv_id}/messages")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert any(m.get("text") == "first check-in" for m in messages)


@pytest.mark.asyncio
async def test_clear_current_agent_oneonone_history(client, tmp_path):
    # Old conversation with history
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone",
        "employee_id": "00100",
        "tools_enabled": True,
    })
    conv_id_old = resp.json()["id"]

    msg_path_old = tmp_path / "employees" / "00100" / "conversations" / conv_id_old / "messages.yaml"
    msg_path_old.parent.mkdir(parents=True, exist_ok=True)
    msg_path_old.write_text(yaml.dump([
        {"sender": "ceo", "role": "CEO", "text": "legacy", "timestamp": "2026-03-19T00:00:00+00:00", "attachments": []},
    ], allow_unicode=True), encoding="utf-8")

    await client.post(f"/api/conversation/{conv_id_old}/close")

    # Start a new current meeting for the same employee
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone",
        "employee_id": "00100",
        "tools_enabled": True,
        "reuse_existing": False,
    })
    conv_id_current = resp.json()["id"]
    assert conv_id_current != conv_id_old

    # Clear current agent's 1-on-1 history
    resp = await client.post(f"/api/conversation/{conv_id_current}/clear")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cleared"
    assert data["employee_id"] == "00100"

    # Old history should be wiped on disk
    assert yaml.safe_load(msg_path_old.read_text(encoding="utf-8")) == []

    # Reopen should now pick the current conversation, without bringing old history back
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone",
        "employee_id": "00100",
        "tools_enabled": True,
    })
    assert resp.status_code == 200
    assert resp.json()["id"] == conv_id_current


@pytest.mark.asyncio
async def test_create_invalid_type(client):
    resp = await client.post("/api/conversation/create", json={
        "type": "invalid", "employee_id": "00100",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_invalid_phase(client):
    resp = await client.get("/api/conversations?phase=bogus")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_send_empty_text(client):
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
    })
    conv_id = resp.json()["id"]

    resp = await client.post(f"/api/conversation/{conv_id}/message", json={"text": ""})
    assert resp.status_code == 400

    resp = await client.post(f"/api/conversation/{conv_id}/message", json={})
    assert resp.status_code == 400
