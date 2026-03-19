"""TaskTreeManager — FIFO queue for concurrent-safe tree modifications.

All tree mutations go through submit(TreeEvent). A single consumer
coroutine processes events serially: modify tree → save → broadcast.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from onemancompany.core.config import SYSTEM_AGENT, TASK_TREE_FILENAME
from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.models import EventType
from onemancompany.core.task_tree import TaskTree


@dataclass
class TreeEvent:
    """A single tree mutation event."""

    type: str  # "node_added" | "node_updated" | "node_accepted" | "node_rejected" | "node_failed"
    node_id: str  # target node (or parent for node_added)
    data: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


class TaskTreeManager:
    """Manages a TaskTree with FIFO event queue for concurrent safety."""

    def __init__(self, project_id: str, project_dir: str) -> None:
        self.project_id = project_id
        self.project_dir = project_dir
        self._tree: TaskTree | None = None
        self._queue: asyncio.Queue[TreeEvent | None] = asyncio.Queue()
        self._consumer_task: asyncio.Task | None = None
        self._started = False

    def _ensure_started(self) -> None:
        if not self._started:
            self._consumer_task = asyncio.create_task(self._consume())
            self._started = True

    @property
    def tree(self) -> TaskTree | None:
        return self._tree

    def load(self) -> TaskTree:
        """Load tree from disk."""
        path = Path(self.project_dir) / TASK_TREE_FILENAME
        if path.exists():
            self._tree = TaskTree.load(path, project_id=self.project_id)
        else:
            self._tree = TaskTree(project_id=self.project_id)
        return self._tree

    async def submit(self, event: TreeEvent) -> None:
        """Submit a tree mutation event to the FIFO queue."""
        self._ensure_started()
        await self._queue.put(event)

    async def stop(self) -> None:
        """Signal consumer to stop and wait for it."""
        if self._started:
            await self._queue.put(None)  # sentinel
            if self._consumer_task:
                await self._consumer_task
            self._started = False

    async def _consume(self) -> None:
        """Process events serially from the queue."""
        while True:
            event = await self._queue.get()
            if event is None:
                break
            try:
                await self._process_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("TreeManager event processing failed: {}", e)

    async def _process_event(self, event: TreeEvent) -> None:
        """Apply a single event to the tree, save, and broadcast."""
        if self._tree is None:
            logger.warning("No tree loaded for project {}", self.project_id)
            return

        node = self._tree.get_node(event.node_id)

        if event.type == "node_added":
            # node_id is the parent; data has child info
            self._tree.add_child(
                parent_id=event.node_id,
                employee_id=event.data.get("employee_id", ""),
                description=event.data.get("description", ""),
                acceptance_criteria=event.data.get("acceptance_criteria", []),
            )
        elif event.type == "node_updated" and node:
            for key, value in event.data.items():
                if hasattr(node, key):
                    setattr(node, key, value)
        elif event.type == "node_accepted" and node:
            node.set_status(TaskPhase.ACCEPTED)
            node.acceptance_result = {"passed": True, "notes": event.data.get("notes", "")}
        elif event.type == "node_rejected" and node:
            node.acceptance_result = {"passed": False, "notes": event.data.get("reason", "")}
            node.set_status(TaskPhase.FAILED if not event.data.get("retry") else TaskPhase.PENDING)
        elif event.type == "node_failed" and node:
            node.set_status(TaskPhase.FAILED)
            node.result = event.data.get("result", node.result)
        else:
            logger.warning("Unknown tree event type or missing node: {} {}", event.type, event.node_id)
            return

        self._save()
        await self._broadcast(event)

    def _save(self) -> None:
        """Persist tree to disk."""
        if self._tree:
            path = Path(self.project_dir) / TASK_TREE_FILENAME
            self._tree.save(path)

    async def _broadcast(self, event: TreeEvent) -> None:
        """Publish tree update to WebSocket via event bus."""
        from onemancompany.core.events import CompanyEvent, event_bus

        await event_bus.publish(
            CompanyEvent(
                type=EventType.TREE_UPDATE,
                payload={
                    "project_id": self.project_id,
                    "event_type": event.type,
                    "node_id": event.node_id,
                    "data": event.data,
                    "timestamp": event.timestamp,
                },
                agent=SYSTEM_AGENT,
            )
        )
