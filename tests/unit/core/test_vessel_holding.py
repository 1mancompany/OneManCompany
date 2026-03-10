"""Tests for HOLDING mechanism in vessel.py.

Covers:
- _parse_holding_metadata: parsing __HOLDING: prefix from agent result
- _setup_reply_poller: cron setup for reply polling
- _execute_task integration: HOLDING detection skips post-task cleanup
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from onemancompany.core.vessel import _parse_holding_metadata


# ---------------------------------------------------------------------------
# TestParseHoldingMetadata
# ---------------------------------------------------------------------------

class TestParseHoldingMetadata:
    """Test _parse_holding_metadata function."""

    def test_not_holding_returns_none(self):
        assert _parse_holding_metadata("Just a normal result") is None

    def test_none_input_returns_none(self):
        assert _parse_holding_metadata(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_holding_metadata("") is None

    def test_empty_holding_returns_empty_dict(self):
        result = _parse_holding_metadata("__HOLDING:")
        assert result == {}

    def test_single_key_value(self):
        result = _parse_holding_metadata("__HOLDING:thread_id=abc123")
        assert result == {"thread_id": "abc123"}

    def test_multiple_key_values(self):
        result = _parse_holding_metadata("__HOLDING:thread_id=abc123,interval=5m")
        assert result == {"thread_id": "abc123", "interval": "5m"}

    def test_trailing_content_after_newline_ignored(self):
        result = _parse_holding_metadata("__HOLDING:thread_id=abc123\nSome extra content")
        assert result == {"thread_id": "abc123"}

    def test_whitespace_in_values_stripped(self):
        result = _parse_holding_metadata("__HOLDING: thread_id = abc123 , interval = 2m ")
        assert result == {"thread_id": "abc123", "interval": "2m"}

    def test_pair_without_equals_ignored(self):
        result = _parse_holding_metadata("__HOLDING:thread_id=abc,badpair,interval=5m")
        assert result == {"thread_id": "abc", "interval": "5m"}

    def test_value_with_equals_sign(self):
        """Value containing = should keep everything after first =."""
        result = _parse_holding_metadata("__HOLDING:key=val=ue")
        assert result == {"key": "val=ue"}

    def test_prefix_must_be_exact(self):
        """__HOLDING without colon should not match."""
        assert _parse_holding_metadata("__HOLDING thread_id=abc") is None

    def test_case_sensitive(self):
        """__holding: (lowercase) should not match."""
        assert _parse_holding_metadata("__holding:thread_id=abc") is None


# ---------------------------------------------------------------------------
# TestSetupReplyPoller
# ---------------------------------------------------------------------------

class TestSetupReplyPoller:
    """Test EmployeeManager._setup_reply_poller method."""

    @patch("onemancompany.core.automation.start_cron")
    def test_calls_start_cron_with_correct_params(self, mock_start_cron):
        mock_start_cron.return_value = {"status": "ok"}
        from onemancompany.core.vessel import EmployeeManager
        mgr = EmployeeManager.__new__(EmployeeManager)
        mgr._setup_reply_poller("00100", "task-001", "thread-abc", "5m")

        mock_start_cron.assert_called_once_with(
            "00100",
            "reply_task-001",
            "5m",
            "[reply_poll] Check Gmail thread thread-abc for task task-001",
        )

    @patch("onemancompany.core.automation.start_cron")
    def test_default_interval(self, mock_start_cron):
        mock_start_cron.return_value = {"status": "ok"}
        from onemancompany.core.vessel import EmployeeManager
        mgr = EmployeeManager.__new__(EmployeeManager)
        mgr._setup_reply_poller("00100", "task-002", "thread-xyz")

        call_args = mock_start_cron.call_args
        assert call_args[0][2] == "1m"  # default interval

    @patch("onemancompany.core.automation.start_cron")
    def test_logs_error_on_failure(self, mock_start_cron):
        mock_start_cron.return_value = {"status": "error", "message": "bad interval"}
        from onemancompany.core.vessel import EmployeeManager
        mgr = EmployeeManager.__new__(EmployeeManager)
        with patch("onemancompany.core.vessel.logger") as mock_logger:
            mgr._setup_reply_poller("00100", "task-003", "thread-fail")
            mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# TestResumeHeldTask
# ---------------------------------------------------------------------------

class TestResumeHeldTask:
    """Test resume_held_task transitions HOLDING → COMPLETE."""

    @pytest.fixture
    def manager_with_holding_task(self):
        from onemancompany.core.vessel import EmployeeManager, AgentTaskBoard, AgentTask
        from onemancompany.core.task_lifecycle import TaskPhase
        mgr = EmployeeManager()
        board = AgentTaskBoard()
        task = AgentTask(id="held1", description="Waiting for human reply")
        task.status = TaskPhase.HOLDING
        task.result = "__HOLDING:thread_id=abc"
        board.tasks.append(task)
        mgr.boards["00010"] = board
        return mgr, task

    @pytest.mark.asyncio
    async def test_resume_sets_complete(self, manager_with_holding_task):
        from onemancompany.core.task_lifecycle import TaskPhase
        mgr, task = manager_with_holding_task
        with patch("onemancompany.core.vessel.persist_task"):
            with patch("onemancompany.core.vessel.archive_task"):
                with patch("onemancompany.core.vessel.stop_cron"):
                    result = await mgr.resume_held_task("00010", "held1", "Human said: looks good!")
        assert result is True
        assert task.status == TaskPhase.COMPLETE
        assert task.result == "Human said: looks good!"

    @pytest.mark.asyncio
    async def test_resume_nonexistent_task(self, manager_with_holding_task):
        mgr, _ = manager_with_holding_task
        result = await mgr.resume_held_task("00010", "nonexistent", "reply")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_non_holding_task(self):
        from onemancompany.core.vessel import EmployeeManager, AgentTaskBoard, AgentTask
        from onemancompany.core.task_lifecycle import TaskPhase
        mgr = EmployeeManager()
        board = AgentTaskBoard()
        task = AgentTask(id="t1", description="Normal task")
        task.status = TaskPhase.PROCESSING
        board.tasks.append(task)
        mgr.boards["00010"] = board
        result = await mgr.resume_held_task("00010", "t1", "reply")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_stops_poller_cron(self, manager_with_holding_task):
        mgr, task = manager_with_holding_task
        with patch("onemancompany.core.vessel.persist_task"):
            with patch("onemancompany.core.vessel.archive_task"):
                with patch("onemancompany.core.vessel.stop_cron") as mock_stop:
                    await mgr.resume_held_task("00010", "held1", "reply")
                    mock_stop.assert_called_once_with("00010", "reply_held1")

    @pytest.mark.asyncio
    async def test_resume_unknown_employee(self):
        from onemancompany.core.vessel import EmployeeManager
        mgr = EmployeeManager()
        result = await mgr.resume_held_task("99999", "t1", "reply")
        assert result is False
