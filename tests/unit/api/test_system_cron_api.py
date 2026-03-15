"""Tests for system cron management API endpoints."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from onemancompany.main import app
    return TestClient(app, raise_server_exceptions=False)


def test_list_system_crons(client):
    fake = [{"name": "heartbeat", "interval": "1m", "description": "HB",
             "running": True, "scope": "system", "employee_id": None,
             "last_run": None, "run_count": 5}]
    with patch("onemancompany.core.system_cron.system_cron_manager") as mock:
        mock.get_all.return_value = fake
        resp = client.get("/api/system/crons")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "heartbeat"


def test_stop_system_cron(client):
    with patch("onemancompany.core.system_cron.system_cron_manager") as mock:
        mock.stop.return_value = {"status": "ok", "name": "heartbeat"}
        resp = client.post("/api/system/crons/heartbeat/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_start_system_cron(client):
    with patch("onemancompany.core.system_cron.system_cron_manager") as mock:
        mock.start.return_value = {"status": "ok", "name": "heartbeat"}
        resp = client.post("/api/system/crons/heartbeat/start")
    assert resp.status_code == 200


def test_update_system_cron_interval(client):
    with patch("onemancompany.core.system_cron.system_cron_manager") as mock:
        mock.update_interval.return_value = {"status": "ok", "name": "heartbeat", "interval": "30s"}
        resp = client.patch("/api/system/crons/heartbeat", json={"interval": "30s"})
    assert resp.status_code == 200
    assert resp.json()["interval"] == "30s"
