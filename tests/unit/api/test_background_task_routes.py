"""Tests for background task API endpoints."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


def _make_client():
    from starlette.testclient import TestClient
    from onemancompany.api.routes import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestListBackgroundTasks:

    @patch("onemancompany.api.routes.background_task_manager")
    def test_returns_tasks(self, mock_mgr):
        client = _make_client()
        mock_task = MagicMock()
        mock_task.to_dict.return_value = {
            "id": "t1", "command": "npm dev", "status": "running",
        }
        mock_mgr.get_all.return_value = [mock_task]
        mock_mgr.running_count = 1

        resp = client.get("/api/background-tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) == 1
        assert data["running_count"] == 1
        assert data["max_concurrent"] == 5


class TestGetBackgroundTask:

    @patch("onemancompany.api.routes.background_task_manager")
    def test_returns_task_with_output(self, mock_mgr):
        client = _make_client()
        mock_task = MagicMock()
        mock_task.to_dict.return_value = {"id": "t1", "status": "running"}
        mock_mgr.get_task.return_value = mock_task
        mock_mgr.read_output_tail.return_value = "hello world"

        resp = client.get("/api/background-tasks/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task"]["id"] == "t1"
        assert data["output_tail"] == "hello world"

    @patch("onemancompany.api.routes.background_task_manager")
    def test_not_found(self, mock_mgr):
        client = _make_client()
        mock_mgr.get_task.return_value = None

        resp = client.get("/api/background-tasks/nope")
        assert resp.status_code == 404


class TestStopBackgroundTask:

    @patch("onemancompany.api.routes.background_task_manager")
    def test_stop_running(self, mock_mgr):
        client = _make_client()
        mock_mgr.terminate = AsyncMock(return_value=True)

        resp = client.post("/api/background-tasks/t1/stop")
        assert resp.status_code == 200

    @patch("onemancompany.api.routes.background_task_manager")
    def test_stop_not_running(self, mock_mgr):
        client = _make_client()
        mock_mgr.terminate = AsyncMock(return_value=False)

        resp = client.post("/api/background-tasks/nope/stop")
        assert resp.status_code == 409
