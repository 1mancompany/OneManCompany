"""Task tree — unified hierarchical task model.

Each project has one TaskTree persisted as task_tree.yaml.
EA is the root node; children are dispatched subtasks.
Results propagate upward through accept_child/reject_child.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

# Terminal statuses — node won't change further
_TERMINAL = frozenset({"accepted", "failed", "cancelled"})


@dataclass
class TaskNode:
    """Single node in the task tree."""

    id: str = ""
    parent_id: str = ""
    children_ids: list[str] = field(default_factory=list)

    employee_id: str = ""
    description: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    node_type: str = "task"  # "task" | "ceo_prompt" | "ceo_followup"

    status: str = "pending"  # pending → processing → completed → accepted / failed / cancelled
    result: str = ""
    acceptance_result: dict | None = None  # {passed: bool, notes: str}

    project_id: str = ""
    created_at: str = ""
    completed_at: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    timeout_seconds: int = 3600

    branch: int = 0
    branch_active: bool = True

    depends_on: list[str] = field(default_factory=list)
    fail_strategy: str = "block"  # "block" | "continue"

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def is_ceo_node(self) -> bool:
        return self.node_type in ("ceo_prompt", "ceo_followup")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "children_ids": list(self.children_ids),
            "employee_id": self.employee_id,
            "description": self.description,
            "acceptance_criteria": list(self.acceptance_criteria),
            "node_type": self.node_type,
            "status": self.status,
            "result": self.result,
            "acceptance_result": self.acceptance_result,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "timeout_seconds": self.timeout_seconds,
            "branch": self.branch,
            "branch_active": self.branch_active,
            "depends_on": list(self.depends_on),
            "fail_strategy": self.fail_strategy,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TaskNode:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class TaskTree:
    """In-memory task tree with YAML persistence."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.root_id: str = ""
        self._nodes: dict[str, TaskNode] = {}
        self.task_id_map: dict[str, str] = {}  # AgentTask ID → TaskNode ID (snapshot-safe)
        self.current_branch: int = 0

    def create_root(self, employee_id: str, description: str) -> TaskNode:
        node = TaskNode(
            employee_id=employee_id,
            description=description,
            project_id=self.project_id,
        )
        self.root_id = node.id
        self._nodes[node.id] = node
        return node

    def add_child(
        self,
        parent_id: str,
        employee_id: str,
        description: str,
        acceptance_criteria: list[str],
        timeout_seconds: int = 3600,
    ) -> TaskNode:
        parent = self._nodes[parent_id]
        child = TaskNode(
            parent_id=parent_id,
            employee_id=employee_id,
            description=description,
            acceptance_criteria=acceptance_criteria,
            project_id=self.project_id,
            timeout_seconds=timeout_seconds,
        )
        parent.children_ids.append(child.id)
        self._nodes[child.id] = child
        return child

    def get_node(self, node_id: str) -> TaskNode | None:
        return self._nodes.get(node_id)

    def get_children(self, node_id: str) -> list[TaskNode]:
        node = self._nodes.get(node_id)
        if not node:
            return []
        return [self._nodes[cid] for cid in node.children_ids if cid in self._nodes]

    def get_siblings(self, node_id: str) -> list[TaskNode]:
        node = self._nodes.get(node_id)
        if not node or not node.parent_id:
            return []
        parent = self._nodes.get(node.parent_id)
        if not parent:
            return []
        return [
            self._nodes[cid]
            for cid in parent.children_ids
            if cid != node_id and cid in self._nodes
        ]

    def get_ea_node(self):
        """Get the EA node (first task-type child of the CEO root node)."""
        root = self._nodes.get(self.root_id)
        if not root or root.node_type != "ceo_prompt":
            # Legacy tree — root is EA
            return root
        for cid in root.children_ids:
            child = self._nodes.get(cid)
            if child and child.node_type == "task":
                return child
        return None

    def new_branch(self) -> int:
        """Start a new branch: deactivate non-root nodes, increment counter."""
        self.current_branch += 1
        for node in self._nodes.values():
            if node.id != self.root_id:
                node.branch_active = False
        # Root always stays active
        root = self._nodes.get(self.root_id)
        if root:
            root.branch = self.current_branch
            root.branch_active = True
        return self.current_branch

    def get_active_children(self, node_id: str) -> list[TaskNode]:
        """Get only branch_active children of a node."""
        return [c for c in self.get_children(node_id) if c.branch_active]

    def all_children_terminal(self, node_id: str) -> bool:
        children = self.get_active_children(node_id)
        if not children:
            return True
        return all(c.is_terminal for c in children)

    def has_failed_children(self, node_id: str) -> bool:
        return any(c.status == "failed" for c in self.get_active_children(node_id))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "project_id": self.project_id,
            "root_id": self.root_id,
            "current_branch": self.current_branch,
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "task_id_map": dict(self.task_id_map),
        }
        path.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path, project_id: str = "") -> TaskTree:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        tree = cls(project_id=project_id or data.get("project_id", ""))
        tree.root_id = data.get("root_id", "")
        tree.current_branch = data.get("current_branch", 0)
        for nd in data.get("nodes", []):
            node = TaskNode.from_dict(nd)
            tree._nodes[node.id] = node
        tree.task_id_map = data.get("task_id_map", {})
        return tree
