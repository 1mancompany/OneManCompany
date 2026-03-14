"""Tests for HOLDING mechanism in vessel.py.

Covers:
- _parse_holding_metadata: parsing __HOLDING: prefix from agent result
- _setup_holding_watchdog_by_id: cron setup for holding watchdog
- resume_held_task: transition HOLDING → COMPLETE via tree-on-disk
- _restart_holding_pollers: restore holding watchdogs from schedule
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

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
# TestSetupHoldingWatchdog
# ---------------------------------------------------------------------------

class TestSetupHoldingWatchdog:
    """Test EmployeeManager._setup_holding_watchdog_by_id method."""

    @patch("onemancompany.core.automation.start_cron")
    def test_calls_start_cron_with_correct_params_thread(self, mock_start_cron):
        mock_start_cron.return_value = {"status": "ok"}
        from onemancompany.core.vessel import EmployeeManager
        mgr = EmployeeManager.__new__(EmployeeManager)
        mgr._setup_holding_watchdog_by_id(
            "00100", "task-001", "2026-01-01T00:00:00",
            {"thread_id": "thread-abc", "interval": "5m"},
        )

        mock_start_cron.assert_called_once_with(
            "00100",
            "reply_task-001",
            "5m",
            "[reply_poll] Check Gmail thread thread-abc for task task-001",
        )

    @patch("onemancompany.core.automation.start_cron")
    def test_default_interval_thread(self, mock_start_cron):
        mock_start_cron.return_value = {"status": "ok"}
        from onemancompany.core.vessel import EmployeeManager
        mgr = EmployeeManager.__new__(EmployeeManager)
        mgr._setup_holding_watchdog_by_id(
            "00100", "task-002", "2026-01-01T00:00:00",
            {"thread_id": "thread-xyz"},
        )

        call_args = mock_start_cron.call_args
        assert call_args[0][2] == "1m"  # default interval for thread_id holds

    @patch("onemancompany.core.automation.start_cron")
    def test_logs_error_on_failure(self, mock_start_cron):
        mock_start_cron.return_value = {"status": "error", "message": "bad interval"}
        from onemancompany.core.vessel import EmployeeManager
        mgr = EmployeeManager.__new__(EmployeeManager)
        with patch("onemancompany.core.vessel.logger") as mock_logger:
            mgr._setup_holding_watchdog_by_id(
                "00100", "task-003", "2026-01-01T00:00:00",
                {"thread_id": "thread-fail"},
            )
            mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# TestResumeHeldTask
# ---------------------------------------------------------------------------

class TestResumeHeldTask:
    """Test resume_held_task transitions HOLDING → COMPLETE via tree-on-disk."""

    @pytest.fixture
    def holding_tree(self, tmp_path):
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry, EmployeeManager
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree("proj1")
        root = tree.create_root("00010", "Waiting for human reply")
        root.status = TaskPhase.HOLDING.value
        root.result = "__HOLDING:thread_id=abc"
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mgr._schedule["00010"] = [entry]
        return mgr, root.id, tree_path

    @pytest.mark.asyncio
    async def test_resume_sets_complete(self, holding_tree):
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase

        mgr, node_id, tree_path = holding_tree
        with patch("onemancompany.core.vessel.stop_cron"):
            result = await mgr.resume_held_task("00010", node_id, "Human said: looks good!")
        assert result is True
        reloaded = TaskTree.load(tree_path, skeleton_only=False)
        node = reloaded.get_node(node_id)
        assert node.status == TaskPhase.COMPLETED.value
        assert node.result == "Human said: looks good!"

    @pytest.mark.asyncio
    async def test_resume_nonexistent_task(self, holding_tree):
        mgr, _, _ = holding_tree
        result = await mgr.resume_held_task("00010", "nonexistent", "reply")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_non_holding_task(self, tmp_path):
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry, EmployeeManager
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree("proj1")
        root = tree.create_root("00010", "Normal task")
        root.status = TaskPhase.PROCESSING.value
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mgr._schedule["00010"] = [entry]

        result = await mgr.resume_held_task("00010", root.id, "reply")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_stops_poller_cron(self, holding_tree):
        mgr, node_id, _ = holding_tree
        with patch("onemancompany.core.vessel.stop_cron") as mock_stop:
            await mgr.resume_held_task("00010", node_id, "reply")
            # Stops both reply_ and holding_ crons
            assert mock_stop.call_count == 2
            mock_stop.assert_any_call("00010", f"reply_{node_id}")
            mock_stop.assert_any_call("00010", f"holding_{node_id}")

    @pytest.mark.asyncio
    async def test_resume_unknown_employee(self):
        from onemancompany.core.vessel import EmployeeManager
        mgr = EmployeeManager()
        result = await mgr.resume_held_task("99999", "t1", "reply")
        assert result is False


# ---------------------------------------------------------------------------
# TestHoldingRestoration
# ---------------------------------------------------------------------------

class TestHoldingRestoration:
    """Test that HOLDING tasks survive restart with cron re-setup."""

    def test_restart_holding_pollers_starts_crons(self, tmp_path):
        """_restart_holding_pollers should call _setup_holding_watchdog_by_id for HOLDING nodes."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry, EmployeeManager
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree("proj1")
        root = tree.create_root("00010", "Waiting")
        root.status = TaskPhase.HOLDING.value
        root.result = "__HOLDING:thread_id=abc123"
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mgr._schedule["00010"] = [entry]

        with patch.object(mgr, "_setup_holding_watchdog_by_id") as mock_setup:
            count = mgr._restart_holding_pollers()
        assert count == 1
        mock_setup.assert_called_once_with("00010", root.id, root.created_at, {"thread_id": "abc123"})

    def test_restart_holding_pollers_skips_non_holding(self, tmp_path):
        """Should not start pollers for non-HOLDING nodes."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry, EmployeeManager
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree("proj1")
        root = tree.create_root("00010", "Normal")
        root.status = TaskPhase.PENDING.value
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mgr._schedule["00010"] = [entry]

        with patch.object(mgr, "_setup_holding_watchdog_by_id") as mock_setup:
            count = mgr._restart_holding_pollers()
        assert count == 0
        mock_setup.assert_not_called()

    def test_restart_holding_pollers_uses_custom_interval(self, tmp_path):
        """Should use interval from metadata if present."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry, EmployeeManager
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree("proj1")
        root = tree.create_root("00010", "Waiting")
        root.status = TaskPhase.HOLDING.value
        root.result = "__HOLDING:thread_id=xyz,interval=5m"
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mgr._schedule["00010"] = [entry]

        with patch.object(mgr, "_setup_holding_watchdog_by_id") as mock_setup:
            count = mgr._restart_holding_pollers()
        assert count == 1
        mock_setup.assert_called_once_with("00010", root.id, root.created_at, {"thread_id": "xyz", "interval": "5m"})

    def test_restart_skips_holding_without_metadata(self, tmp_path):
        """HOLDING nodes without metadata should not get a watchdog."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry, EmployeeManager
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree("proj1")
        root = tree.create_root("00010", "Waiting")
        root.status = TaskPhase.HOLDING.value
        root.result = "__HOLDING:"
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mgr._schedule["00010"] = [entry]

        with patch.object(mgr, "_setup_holding_watchdog_by_id") as mock_setup:
            count = mgr._restart_holding_pollers()
        assert count == 0
        mock_setup.assert_not_called()


# ---------------------------------------------------------------------------
# TestHoldingIntegration
# ---------------------------------------------------------------------------

class TestHoldingIntegration:
    """Full flow: result → HOLDING → resume → COMPLETE."""

    @pytest.mark.asyncio
    async def test_full_holding_flow(self, tmp_path):
        """Test: __HOLDING: result → HOLDING status → resume → COMPLETE."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import (
            _parse_holding_metadata,
            ScheduleEntry,
            EmployeeManager,
        )
        from onemancompany.core.task_lifecycle import TaskPhase

        # 1. Parse holding metadata
        result = "__HOLDING:thread_id=gmail_thread_123,interval=2m"
        meta = _parse_holding_metadata(result)
        assert meta == {"thread_id": "gmail_thread_123", "interval": "2m"}

        # 2. Create tree with a HOLDING node
        tree = TaskTree("proj1")
        root = tree.create_root("00010", "Send task to human")
        root.status = TaskPhase.HOLDING.value
        root.result = result
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        # 3. Setup manager with the holding node
        mgr = EmployeeManager()
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mgr._schedule["00010"] = [entry]

        # 4. Verify watchdog setup
        with patch("onemancompany.core.automation.start_cron") as mock_start:
            mock_start.return_value = {"status": "ok"}
            mgr._setup_holding_watchdog_by_id("00010", root.id, root.created_at, meta)
            mock_start.assert_called_once_with(
                "00010", f"reply_{root.id}", "2m",
                f"[reply_poll] Check Gmail thread gmail_thread_123 for task {root.id}",
            )

        # 5. Resume the held task
        with patch("onemancompany.core.vessel.stop_cron") as mock_stop:
            ok = await mgr.resume_held_task("00010", root.id, "Human replied: All tests pass!")
            assert mock_stop.call_count == 2

        # 6. Verify final state
        assert ok is True
        reloaded = TaskTree.load(tree_path, skeleton_only=False)
        node = reloaded.get_node(root.id)
        assert node.status == TaskPhase.COMPLETED.value
        assert node.result == "Human replied: All tests pass!"
        assert node.completed_at != ""

    @pytest.mark.asyncio
    async def test_holding_restoration_flow(self, tmp_path):
        """Test: HOLDING node survives restart and poller restarts."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import (
            ScheduleEntry,
            EmployeeManager,
        )
        from onemancompany.core.task_lifecycle import TaskPhase

        # Simulate a restored HOLDING node
        tree = TaskTree("proj1")
        root = tree.create_root("00010", "Waiting for human")
        root.status = TaskPhase.HOLDING.value
        root.result = "__HOLDING:thread_id=thread_xyz,interval=3m"
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mgr._schedule["00010"] = [entry]

        # _restart_holding_pollers should set up watchdog
        with patch.object(mgr, "_setup_holding_watchdog_by_id") as mock_setup:
            count = mgr._restart_holding_pollers()

        assert count == 1
        mock_setup.assert_called_once_with("00010", root.id, root.created_at, {"thread_id": "thread_xyz", "interval": "3m"})

        # Then resume
        with patch("onemancompany.core.vessel.stop_cron"):
            ok = await mgr.resume_held_task("00010", root.id, "Reply from human after restart")

        assert ok is True
        reloaded = TaskTree.load(tree_path, skeleton_only=False)
        node = reloaded.get_node(root.id)
        assert node.status == TaskPhase.COMPLETED.value
