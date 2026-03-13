"""Tests for CEO inbox REST endpoints."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestGetCeoInbox:
    """GET /api/ceo/inbox"""

    @patch("onemancompany.api.routes._scan_ceo_inbox_nodes")
    def test_returns_ceo_nodes(self, mock_scan):
        from starlette.testclient import TestClient
        from onemancompany.api.routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_scan.return_value = [{
            "project_id": "proj1",
            "node_id": "node_abc",
            "description": "Need approval",
            "from_employee_id": "emp001",
            "from_nickname": "小明",
            "status": "pending",
            "created_at": "2026-03-13T10:00:00",
        }]

        resp = client.get("/api/ceo/inbox")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["node_id"] == "node_abc"

    @patch("onemancompany.api.routes._scan_ceo_inbox_nodes")
    def test_returns_empty_when_no_nodes(self, mock_scan):
        from starlette.testclient import TestClient
        from onemancompany.api.routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_scan.return_value = []
        resp = client.get("/api/ceo/inbox")
        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestSendMessage:
    """POST /api/ceo/inbox/{node_id}/message"""

    @patch("onemancompany.api.routes.get_session")
    def test_rejects_if_no_session(self, mock_get):
        from starlette.testclient import TestClient
        from onemancompany.api.routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_get.return_value = None
        resp = client.post("/api/ceo/inbox/xyz/message", json={"text": "hi"})
        assert resp.status_code == 404


class TestCompleteConversation:
    """POST /api/ceo/inbox/{node_id}/complete"""

    @patch("onemancompany.api.routes.get_session")
    def test_rejects_if_no_session(self, mock_get):
        from starlette.testclient import TestClient
        from onemancompany.api.routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_get.return_value = None
        resp = client.post("/api/ceo/inbox/xyz/complete")
        assert resp.status_code == 404
