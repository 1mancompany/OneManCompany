"""Tests for TaskTreeManager — FIFO queue for concurrent tree modifications."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from onemancompany.core.task_tree import TaskTree


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
