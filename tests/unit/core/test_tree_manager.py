"""Tests for TaskTreeManager — FIFO queue for concurrent tree modifications."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.task_tree import TaskTree
from onemancompany.core.tree_manager import TaskTreeManager, TreeEvent


class TestTreeEvent:
    def test_timestamp_auto_set(self):
        ev = TreeEvent(type="node_updated", node_id="n1")
        assert ev.timestamp != ""

    def test_timestamp_preserved(self):
        ev = TreeEvent(type="node_updated", node_id="n1", timestamp="2026-01-01T00:00:00")
        assert ev.timestamp == "2026-01-01T00:00:00"


class TestTaskTreeManager:
    @pytest.mark.asyncio
    async def test_submit_and_process_event(self):
        """Events submitted to the queue are processed serially."""
        from onemancompany.core.tree_manager import TaskTreeManager, TreeEvent

        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")

        mgr = TaskTreeManager(project_id="proj1", project_dir="/tmp/proj")
        mgr._tree = tree

        with patch.object(mgr, "_save") as mock_save, \
             patch.object(mgr, "_broadcast", new_callable=AsyncMock) as mock_broadcast:
            await mgr.submit(TreeEvent(
                type="node_updated",
                node_id=root.id,
                data={"status": "completed", "result": "done"},
            ))
            await asyncio.sleep(0.1)
            await mgr.stop()

        assert root.status == "completed"
        assert root.result == "done"
        mock_save.assert_called()
        mock_broadcast.assert_called()

    @pytest.mark.asyncio
    async def test_concurrent_events_processed_serially(self):
        """Multiple concurrent submits are processed one at a time."""
        from onemancompany.core.tree_manager import TaskTreeManager, TreeEvent

        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c2 = tree.add_child(root.id, "00011", "B", [])

        mgr = TaskTreeManager(project_id="proj1", project_dir="/tmp/proj")
        mgr._tree = tree

        order = []
        original_process = mgr._process_event

        async def tracking_process(event):
            order.append(event.node_id)
            await original_process(event)

        mgr._process_event = tracking_process

        with patch.object(mgr, "_save"), \
             patch.object(mgr, "_broadcast", new_callable=AsyncMock):
            await mgr.submit(TreeEvent(type="node_updated", node_id=c1.id, data={"status": "completed"}))
            await mgr.submit(TreeEvent(type="node_updated", node_id=c2.id, data={"status": "completed"}))
            await asyncio.sleep(0.1)
            await mgr.stop()

        assert order == [c1.id, c2.id]

    @pytest.mark.asyncio
    async def test_node_added_event(self):
        """node_added creates a new child node."""
        from onemancompany.core.tree_manager import TaskTreeManager, TreeEvent

        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")

        mgr = TaskTreeManager(project_id="proj1", project_dir="/tmp/proj")
        mgr._tree = tree

        with patch.object(mgr, "_save"), \
             patch.object(mgr, "_broadcast", new_callable=AsyncMock):
            await mgr.submit(TreeEvent(
                type="node_added",
                node_id=root.id,
                data={"employee_id": "00010", "description": "new child", "acceptance_criteria": ["done"]},
            ))
            await asyncio.sleep(0.1)
            await mgr.stop()

        children = tree.get_children(root.id)
        assert len(children) == 1
        assert children[0].employee_id == "00010"

    @pytest.mark.asyncio
    async def test_tree_property(self):
        mgr = TaskTreeManager(project_id="p1", project_dir="/tmp/p")
        assert mgr.tree is None
        tree = TaskTree(project_id="p1")
        mgr._tree = tree
        assert mgr.tree is tree

    @pytest.mark.asyncio
    async def test_load_existing_tree(self, tmp_path):
        """load() reads tree from disk when file exists."""
        tree = TaskTree(project_id="proj1")
        tree.create_root("00001", "Root")
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = TaskTreeManager(project_id="proj1", project_dir=str(tmp_path))
        with patch("onemancompany.core.tree_manager.TASK_TREE_FILENAME", "task_tree.yaml"):
            loaded = mgr.load()
        assert loaded is not None
        assert loaded.project_id == "proj1"

    @pytest.mark.asyncio
    async def test_load_creates_new_tree(self, tmp_path):
        """load() creates a fresh tree when file doesn't exist."""
        mgr = TaskTreeManager(project_id="proj2", project_dir=str(tmp_path))
        with patch("onemancompany.core.tree_manager.TASK_TREE_FILENAME", "task_tree.yaml"):
            loaded = mgr.load()
        assert loaded is not None
        assert loaded.project_id == "proj2"

    @pytest.mark.asyncio
    async def test_consume_error_handling(self):
        """Errors in _process_event don't crash the consumer loop."""
        tree = TaskTree(project_id="p")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])

        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        mgr._tree = tree

        call_count = 0
        original_process = mgr._process_event

        async def failing_then_ok(event):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            await original_process(event)

        mgr._process_event = failing_then_ok

        with patch.object(mgr, "_save"), \
             patch.object(mgr, "_broadcast", new_callable=AsyncMock):
            # First event will error, second should still process
            await mgr.submit(TreeEvent(type="node_updated", node_id=root.id, data={"status": "completed"}))
            await mgr.submit(TreeEvent(type="node_updated", node_id=child.id, data={"status": "completed"}))
            await asyncio.sleep(0.1)
            await mgr.stop()

        assert call_count == 2
        assert child.status == "completed"

    @pytest.mark.asyncio
    async def test_process_event_no_tree(self):
        """_process_event warns when tree is None."""
        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        mgr._tree = None
        # Should not raise, just log warning
        await mgr._process_event(TreeEvent(type="node_updated", node_id="n1", data={}))

    @pytest.mark.asyncio
    async def test_node_accepted_event(self):
        tree = TaskTree(project_id="p")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        # Move child to processing then completed so accepted is valid
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)

        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        mgr._tree = tree

        with patch.object(mgr, "_save"), \
             patch.object(mgr, "_broadcast", new_callable=AsyncMock):
            await mgr.submit(TreeEvent(
                type="node_accepted",
                node_id=child.id,
                data={"notes": "looks good"},
            ))
            await asyncio.sleep(0.1)
            await mgr.stop()

        assert child.status == TaskPhase.ACCEPTED.value
        assert child.acceptance_result["passed"] is True
        assert child.acceptance_result["notes"] == "looks good"

    @pytest.mark.asyncio
    async def test_node_rejected_event_no_retry(self):
        tree = TaskTree(project_id="p")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)

        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        mgr._tree = tree

        with patch.object(mgr, "_save"), \
             patch.object(mgr, "_broadcast", new_callable=AsyncMock):
            await mgr.submit(TreeEvent(
                type="node_rejected",
                node_id=child.id,
                data={"reason": "bad", "retry": False},
            ))
            await asyncio.sleep(0.1)
            await mgr.stop()

        assert child.status == TaskPhase.FAILED.value
        assert child.acceptance_result["passed"] is False

    @pytest.mark.asyncio
    async def test_node_rejected_event_with_retry(self):
        tree = TaskTree(project_id="p")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)

        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        mgr._tree = tree

        with patch.object(mgr, "_save"), \
             patch.object(mgr, "_broadcast", new_callable=AsyncMock):
            await mgr.submit(TreeEvent(
                type="node_rejected",
                node_id=child.id,
                data={"reason": "retry me", "retry": True},
            ))
            await asyncio.sleep(0.1)
            await mgr.stop()

        assert child.status == TaskPhase.PENDING.value

    @pytest.mark.asyncio
    async def test_node_failed_event(self):
        tree = TaskTree(project_id="p")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)

        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        mgr._tree = tree

        with patch.object(mgr, "_save"), \
             patch.object(mgr, "_broadcast", new_callable=AsyncMock):
            await mgr.submit(TreeEvent(
                type="node_failed",
                node_id=child.id,
                data={"result": "crashed"},
            ))
            await asyncio.sleep(0.1)
            await mgr.stop()

        assert child.status == TaskPhase.FAILED.value
        assert child.result == "crashed"

    @pytest.mark.asyncio
    async def test_unknown_event_type(self):
        """Unknown event type logs warning and does NOT save/broadcast."""
        tree = TaskTree(project_id="p")
        root = tree.create_root("00001", "Root")

        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        mgr._tree = tree

        with patch.object(mgr, "_save") as mock_save, \
             patch.object(mgr, "_broadcast", new_callable=AsyncMock) as mock_broadcast:
            await mgr.submit(TreeEvent(
                type="bogus_event",
                node_id=root.id,
                data={},
            ))
            await asyncio.sleep(0.1)
            await mgr.stop()

        mock_save.assert_not_called()
        mock_broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_node(self):
        """Event referencing a non-existent node triggers warning branch."""
        tree = TaskTree(project_id="p")
        tree.create_root("00001", "Root")

        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        mgr._tree = tree

        with patch.object(mgr, "_save") as mock_save, \
             patch.object(mgr, "_broadcast", new_callable=AsyncMock) as mock_broadcast:
            await mgr.submit(TreeEvent(
                type="node_updated",
                node_id="nonexistent",
                data={"status": "completed"},
            ))
            await asyncio.sleep(0.1)
            await mgr.stop()

        # Falls through to the else branch (unknown event / missing node)
        mock_save.assert_not_called()
        mock_broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_persists_tree(self, tmp_path):
        tree = TaskTree(project_id="p")
        tree.create_root("00001", "Root")

        mgr = TaskTreeManager(project_id="p", project_dir=str(tmp_path))
        mgr._tree = tree

        with patch("onemancompany.core.tree_manager.TASK_TREE_FILENAME", "task_tree.yaml"):
            mgr._save()

        assert (tmp_path / "task_tree.yaml").exists()

    @pytest.mark.asyncio
    async def test_save_no_tree(self):
        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        mgr._tree = None
        mgr._save()  # Should not raise

    @pytest.mark.asyncio
    async def test_broadcast_publishes_event(self):
        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        event = TreeEvent(type="node_updated", node_id="n1", data={"key": "val"})

        with patch("onemancompany.core.tree_manager.event_bus", create=True) as mock_bus:
            mock_bus.publish = AsyncMock()
            # Need to import inside the method, so patch at the events module level
            with patch("onemancompany.core.events.event_bus") as inner_bus:
                inner_bus.publish = AsyncMock()
                await mgr._broadcast(event)
                inner_bus.publish.assert_called_once()
                call_args = inner_bus.publish.call_args[0][0]
                assert call_args.payload["project_id"] == "p"
                assert call_args.payload["event_type"] == "node_updated"

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self):
        """stop() is a no-op when manager was never started."""
        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        await mgr.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_consume_reraises_cancelled_error(self):
        """CancelledError in _process_event is re-raised, not swallowed."""
        mgr = TaskTreeManager(project_id="p", project_dir="/tmp/p")
        mgr._tree = TaskTree(project_id="p")

        async def cancel_process(event):
            raise asyncio.CancelledError()

        mgr._process_event = cancel_process
        mgr._ensure_started()
        await mgr._queue.put(TreeEvent(type="node_updated", node_id="n1", data={}))
        # The consumer task should be cancelled
        await asyncio.sleep(0.1)
        assert mgr._consumer_task.done()
        mgr._started = False
