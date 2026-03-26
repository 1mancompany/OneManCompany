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
    def test_completes_via_session(self, mock_get):
        from starlette.testclient import TestClient
        from onemancompany.api.routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_session = MagicMock()
        mock_session.complete = AsyncMock()
        mock_get.return_value = mock_session

        resp = client.post("/api/ceo/inbox/xyz/complete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completing"
        mock_session.complete.assert_awaited_once()

    @patch("onemancompany.api.routes._complete_ceo_request", new_callable=AsyncMock)
    @patch("onemancompany.api.routes._find_ceo_node")
    @patch("onemancompany.api.routes.get_session")
    def test_fallback_completes_node_directly_when_no_session(self, mock_get, mock_find, mock_complete):
        from starlette.testclient import TestClient
        from onemancompany.api.routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_get.return_value = None
        mock_node = MagicMock()
        mock_node.status = "processing"
        mock_find.return_value = (mock_node, MagicMock(), "/tmp/proj")

        resp = client.post("/api/ceo/inbox/node123/complete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        mock_complete.assert_awaited_once()

    @patch("onemancompany.api.routes._find_ceo_node")
    @patch("onemancompany.api.routes.get_session")
    def test_rejects_already_finished_node(self, mock_get, mock_find):
        from starlette.testclient import TestClient
        from onemancompany.api.routes import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        mock_get.return_value = None
        mock_node = MagicMock()
        mock_node.status = "finished"
        mock_find.return_value = (mock_node, MagicMock(), "/tmp/proj")

        resp = client.post("/api/ceo/inbox/node123/complete")
        assert resp.status_code == 409
        assert "already finished" in resp.json()["detail"]
