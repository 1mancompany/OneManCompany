"""Tests for background task agent tools."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestStartBackgroundTask:

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_start_returns_task_id(self, mock_mgr):
        from onemancompany.agents.common_tools import start_background_task

        mock_task = MagicMock()
        mock_task.id = "abc12345"
        mock_task.pid = 999
        mock_mgr.launch = AsyncMock(return_value=mock_task)

        result = await start_background_task.ainvoke({
            "command": "npm run dev",
            "description": "Dev server",
            "employee_id": "emp001",
        })
        assert result["status"] == "ok"
        assert result["task_id"] == "abc12345"

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_start_returns_error_at_limit(self, mock_mgr):
        from onemancompany.agents.common_tools import start_background_task

        mock_mgr.launch = AsyncMock(side_effect=RuntimeError("limit reached"))
        result = await start_background_task.ainvoke({
            "command": "npm run dev",
            "description": "Dev server",
            "employee_id": "emp001",
        })
        assert result["status"] == "error"
        assert "limit" in result["message"]


class TestCheckBackgroundTask:

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_check_returns_status(self, mock_mgr):
        from onemancompany.agents.common_tools import check_background_task

        mock_task = MagicMock()
        mock_task.status = "running"
        mock_task.port = 3000
        mock_task.address = "http://localhost:3000"
        mock_task.returncode = None
        mock_task.started_at = "2026-03-26T14:00:00"
        mock_task.ended_at = None
        mock_task.pid = 12345
        mock_mgr.get_task.return_value = mock_task
        mock_mgr.read_output_tail.return_value = "server started"

        result = await check_background_task.ainvoke({
            "task_id": "abc12345",
            "employee_id": "emp001",
        })
        assert result["status"] == "running"
        assert result["port"] == 3000
        assert "server started" in result["output_tail"]
        assert "uptime_seconds" in result

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_check_not_found(self, mock_mgr):
        from onemancompany.agents.common_tools import check_background_task

        mock_mgr.get_task.return_value = None
        result = await check_background_task.ainvoke({
            "task_id": "nope",
            "employee_id": "emp001",
        })
        assert result["status"] == "error"


class TestStopBackgroundTask:

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_stop_running_task(self, mock_mgr):
        from onemancompany.agents.common_tools import stop_background_task

        mock_mgr.terminate = AsyncMock(return_value=True)
        result = await stop_background_task.ainvoke({
            "task_id": "abc12345",
            "employee_id": "emp001",
        })
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_stop_not_found(self, mock_mgr):
        from onemancompany.agents.common_tools import stop_background_task

        mock_mgr.terminate = AsyncMock(return_value=False)
        result = await stop_background_task.ainvoke({
            "task_id": "nope",
            "employee_id": "emp001",
        })
        assert result["status"] == "error"
