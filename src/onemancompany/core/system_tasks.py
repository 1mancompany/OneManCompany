"""System task tree — lightweight tree for cron/ad-hoc tasks per employee.

One per employee, persisted to employees/{id}/system_tasks.yaml.
Auto-cleans finished/cancelled nodes older than 24h on save.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import yaml
from loguru import logger

from onemancompany.core.task_lifecycle import TaskPhase, RESOLVED
from onemancompany.core.task_tree import TaskNode

_CLEANUP_AGE = timedelta(hours=24)


class SystemTaskTree:
    """Per-employee tree for non-project tasks. Auto-cleans old resolved nodes on save."""

    def __init__(self, employee_id: str) -> None:
        self.employee_id = employee_id
        self._nodes: dict[str, TaskNode] = {}

    def create_system_node(self, employee_id: str, description: str) -> TaskNode:
        node = TaskNode(
            employee_id=employee_id,
            description=description,
            node_type="system",
        )
        self._nodes[node.id] = node
        return node

    def get_node(self, node_id: str) -> TaskNode | None:
        return self._nodes.get(node_id)

    def get_all_nodes(self) -> list[TaskNode]:
        return list(self._nodes.values())

    def get_pending_nodes(self) -> list[TaskNode]:
        return [n for n in self._nodes.values() if n.status == TaskPhase.PENDING.value]

    def save(self, path: Path) -> None:
        self._cleanup_old()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "employee_id": self.employee_id,
            "nodes": [n.to_dict() for n in self._nodes.values()],
        }
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, employee_id: str = "") -> SystemTaskTree:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tree = cls(employee_id=employee_id or data.get("employee_id", ""))
        for nd in data.get("nodes", []):
            node = TaskNode.from_dict(nd)
            tree._nodes[node.id] = node
        return tree

    def _cleanup_old(self) -> None:
        now = datetime.now()
        to_remove = []
        for nid, node in self._nodes.items():
            if TaskPhase(node.status) in RESOLVED and node.completed_at:
                try:
                    completed = datetime.fromisoformat(node.completed_at)
                    if now - completed > _CLEANUP_AGE:
                        to_remove.append(nid)
                except ValueError:
                    logger.warning("Invalid completed_at timestamp for node {}: {}", nid, node.completed_at)
        for nid in to_remove:
            del self._nodes[nid]
