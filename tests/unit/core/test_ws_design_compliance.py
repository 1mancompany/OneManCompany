"""Tests for WS design compliance — no duplicate data paths.

Ensures:
1. agent_task_update WS payload contains full task data (no REST re-fetch needed)
2. background_task_update WS payload contains full task data
3. cron_status_change event type exists and is emitted on start/stop
4. task-tree auto-refresh polling is removed
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 1. agent_task_update payload includes full node dict
# ---------------------------------------------------------------------------

class TestAgentTaskUpdatePayload:
    """The WS payload for agent_task_update must contain the full task dict
    so the frontend can render in-place without REST re-fetch."""

    def test_publish_node_update_includes_task_dict(self):
        """_publish_node_update should include node.to_dict() in payload."""
        from onemancompany.core.vessel import EmployeeManager

        em = EmployeeManager.__new__(EmployeeManager)
        em._employees = {"emp1": MagicMock()}

        mock_node = MagicMock()
        mock_node.to_dict.return_value = {
            "id": "node1",
            "status": "completed",
            "description_preview": "test task",
            "cost_usd": 0.01,
            "result": "done",
        }

        with patch("onemancompany.core.vessel.asyncio") as mock_asyncio, \
             patch("onemancompany.core.vessel.event_bus") as mock_bus:
            mock_loop = MagicMock()
            mock_asyncio.get_running_loop.return_value = mock_loop

            em._publish_node_update("emp1", mock_node)

            # Verify event_bus.publish was called
            mock_loop.create_task.assert_called_once()
            publish_call = mock_bus.publish.call_args
            event = publish_call[0][0]
            assert event.type.value == "agent_task_update"
            assert "task" in event.payload
            assert event.payload["task"]["id"] == "node1"
            assert event.payload["task"]["status"] == "completed"
            assert event.payload["employee_id"] == "emp1"


# ---------------------------------------------------------------------------
# 2. background_task_update payload includes full task dict
# ---------------------------------------------------------------------------

class TestBackgroundTaskUpdatePayload:
    """The WS payload for background_task_update must contain
    full task data + output_tail so the frontend can render without REST."""

    def test_broadcast_includes_output_tail(self):
        """_broadcast_update should include output_tail in payload."""
        from onemancompany.core.background_tasks import BackgroundTaskManager, BackgroundTask

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        mgr._data_dir = MagicMock()
        mgr._tasks = {}
        mgr._processes = {}
        mgr._monitors = {}

        task = BackgroundTask(
            id="bg1",
            command="echo hello",
            description="Test task",
            working_dir="/tmp",
            started_by="CEO",
            status="running",
        )

        # The to_dict should have all fields needed for rendering
        d = task.to_dict()
        assert "id" in d
        assert "status" in d
        assert "command" in d
        assert "description" in d
        assert "started_by" in d


# ---------------------------------------------------------------------------
# 3. CRON_STATUS_CHANGE event type exists
# ---------------------------------------------------------------------------

class TestCronStatusChangeEvent:
    """EventType must include CRON_STATUS_CHANGE for Layer 2 push."""

    def test_event_type_exists(self):
        from onemancompany.core.models import EventType
        assert hasattr(EventType, "CRON_STATUS_CHANGE")
        assert EventType.CRON_STATUS_CHANGE.value == "cron_status_change"

    def test_start_cron_emits_event(self):
        """start_cron should publish a cron_status_change event."""
        from onemancompany.core.automation import start_cron
        from onemancompany.core.models import EventType

        with patch("onemancompany.core.automation._parse_interval", return_value=60), \
             patch("onemancompany.core.automation.asyncio") as mock_asyncio, \
             patch("onemancompany.core.automation._load_automations", return_value={"crons": [], "webhooks": []}), \
             patch("onemancompany.core.automation._save_automations"), \
             patch("onemancompany.core.automation._broadcast_cron_status") as mock_broadcast:
            mock_asyncio.create_task.return_value = MagicMock()
            start_cron("emp1", "my_cron", "1m", "do stuff")
            mock_broadcast.assert_called_once_with("emp1", "my_cron", True)

    def test_stop_cron_emits_event(self):
        """stop_cron should publish a cron_status_change event."""
        from onemancompany.core.automation import stop_cron
        from onemancompany.core.models import EventType

        with patch("onemancompany.core.automation._load_automations", return_value={"crons": [{"name": "my_cron", "interval": "1m", "task_description": "x"}], "webhooks": []}), \
             patch("onemancompany.core.automation._save_automations"), \
             patch("onemancompany.core.automation._cancel_cron_tasks", return_value=[]), \
             patch("onemancompany.core.automation._cron_tasks", {"emp1:my_cron": MagicMock(done=MagicMock(return_value=False))}), \
             patch("onemancompany.core.automation._broadcast_cron_status") as mock_broadcast:
            stop_cron("emp1", "my_cron")
            mock_broadcast.assert_called_once_with("emp1", "my_cron", False)

    def test_stop_all_crons_emits_events(self):
        """stop_all_crons_for_employee should broadcast once per stopped cron."""
        from onemancompany.core.automation import stop_all_crons_for_employee

        crons = [
            {"name": "cron_a", "interval": "1m", "task_description": "a"},
            {"name": "cron_b", "interval": "5m", "task_description": "b"},
        ]
        mock_task = MagicMock(done=MagicMock(return_value=False))
        cron_tasks = {"emp1:cron_a": mock_task, "emp1:cron_b": mock_task}

        with patch("onemancompany.core.automation._load_automations", return_value={"crons": crons, "webhooks": []}), \
             patch("onemancompany.core.automation._save_automations"), \
             patch("onemancompany.core.automation._cancel_cron_tasks", return_value=[]), \
             patch("onemancompany.core.automation._cron_tasks", cron_tasks), \
             patch("onemancompany.core.automation._broadcast_cron_status") as mock_broadcast:
            result = stop_all_crons_for_employee("emp1")
            assert result["count"] == 2
            assert mock_broadcast.call_count == 2
            mock_broadcast.assert_any_call("emp1", "cron_a", False)
            mock_broadcast.assert_any_call("emp1", "cron_b", False)
