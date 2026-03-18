import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def conversation_service(tmp_path, monkeypatch):
    """Create a ConversationService with tmp_path isolation."""
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    from onemancompany.core.conversation import ConversationService
    svc = ConversationService()
    return svc


@pytest.fixture
def patched_app(conversation_service, monkeypatch):
    """Patch _conversation_service in routes to use our test instance."""
    # We need to import routes AFTER patching
    import onemancompany.api.routes as routes_mod
    monkeypatch.setattr(routes_mod, "_conversation_service", conversation_service)
    from onemancompany.main import create_app
    return routes_mod.router


@pytest.mark.asyncio
async def test_create_conversation_api(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    from onemancompany.core.conversation import ConversationService
    svc = ConversationService()

    import onemancompany.api.routes as routes_mod
    monkeypatch.setattr(routes_mod, "_conversation_service", svc)

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(routes_mod.router)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
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
async def test_send_message_api(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    from onemancompany.core.conversation import ConversationService
    svc = ConversationService()

    import onemancompany.api.routes as routes_mod
    monkeypatch.setattr(routes_mod, "_conversation_service", svc)

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(routes_mod.router)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create conversation first
        resp = await client.post("/api/conversation/create", json={
            "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
        })
        conv_id = resp.json()["id"]

        # Send message
        resp = await client.post(f"/api/conversation/{conv_id}/message", json={
            "text": "hello",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"


@pytest.mark.asyncio
async def test_get_messages_api(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    from onemancompany.core.conversation import ConversationService
    svc = ConversationService()

    import onemancompany.api.routes as routes_mod
    monkeypatch.setattr(routes_mod, "_conversation_service", svc)

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(routes_mod.router)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
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
async def test_close_conversation_api(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    from onemancompany.core.conversation import ConversationService
    svc = ConversationService()

    import onemancompany.api.routes as routes_mod
    monkeypatch.setattr(routes_mod, "_conversation_service", svc)

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(routes_mod.router)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/conversation/create", json={
            "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
        })
        conv_id = resp.json()["id"]

        resp = await client.post(f"/api/conversation/{conv_id}/close")
        assert resp.status_code == 200
        assert resp.json()["phase"] == "closed"


@pytest.mark.asyncio
async def test_list_conversations_api(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    from onemancompany.core.conversation import ConversationService
    svc = ConversationService()

    import onemancompany.api.routes as routes_mod
    monkeypatch.setattr(routes_mod, "_conversation_service", svc)

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(routes_mod.router)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/conversation/create", json={
            "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
        })

        resp = await client.get("/api/conversations")
        assert resp.status_code == 200
        convs = resp.json()["conversations"]
        assert len(convs) == 1
