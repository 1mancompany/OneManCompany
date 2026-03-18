"""Task tree — unified hierarchical task model.

Each project has one TaskTree persisted as task_tree.yaml.
EA is the root node; children are dispatched subtasks.
Results propagate upward through accept_child/reject_child.

Tree Registry
-------------
Trees are cached in memory. All code should use ``get_tree(path)``
instead of ``TaskTree.load(path)`` directly, and ``save_tree_async(path)``
instead of ``tree.save(path)``.  This ensures a single in-memory object
per tree file — no stale-read overwrites.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger

from onemancompany.core.task_lifecycle import (
    TaskPhase, transition,
    RESOLVED, DONE_EXECUTING, UNBLOCKS_DEPENDENTS, WILL_NOT_DELIVER,
)

_STATUS_MIGRATION = {"complete": "completed"}


@dataclass
class TaskNode:
    """Single node in the task tree."""

    id: str = ""
    parent_id: str = ""
    children_ids: list[str] = field(default_factory=list)

    employee_id: str = ""
    description: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    node_type: str = "task"  # "task" | "ceo_prompt" | "ceo_followup" | "ceo_request" | "review"

    model_used: str = ""              # which LLM executed
    project_dir: str = ""             # workspace path

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

    # Hold reason: when a tool needs the parent to enter HOLDING after execution,
    # it sets this field (e.g. "blocking_child=<node_id>"). vessel.py checks this
    # generically — no child-type-specific detection needed.
    hold_reason: str = ""

    # --- Content externalization tracking (not part of equality/repr) ---
    _content_dirty: bool = field(default=False, init=False, repr=False, compare=False)
    _content_loaded: bool = field(default=False, init=False, repr=False, compare=False)
    _description_preview: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if self.description:
            self._description_preview = self.description[:200]

    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        if name == "description":
            try:
                super().__setattr__("_content_dirty", True)
                super().__setattr__("_description_preview", (value or "")[:200])
            except AttributeError:
                return  # During __init__ before _content_dirty exists
        elif name == "result":
            try:
                super().__setattr__("_content_dirty", True)
            except AttributeError:
                return  # During __init__ before _content_dirty exists

    @property
    def description_preview(self) -> str:
        return self._description_preview

    def save_content(self, project_dir: Path | str) -> None:
        """Write description/result to a separate content file."""
        if not self._content_dirty:
            return
        nodes_dir = Path(project_dir) / "nodes"
        nodes_dir.mkdir(parents=True, exist_ok=True)
        content = {"description": self.description, "result": self.result}
        (nodes_dir / f"{self.id}.yaml").write_text(
            yaml.dump(content, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._content_dirty = False

    def load_content(self, project_dir: Path | str) -> None:
        """Load description/result from content file (idempotent)."""
        if self._content_loaded:
            return
        content_path = Path(project_dir) / "nodes" / f"{self.id}.yaml"
        if content_path.exists():
            data = yaml.safe_load(content_path.read_text(encoding="utf-8")) or {}
            # Use object.__setattr__ to avoid marking dirty
            desc = data.get("description", "")
            object.__setattr__(self, "description", desc)
            object.__setattr__(self, "result", data.get("result", ""))
            object.__setattr__(self, "_description_preview", (desc or "")[:200])
        self._content_loaded = True

    def set_status(self, target: TaskPhase) -> None:
        """Validated status transition. Raises TaskTransitionError if invalid."""
        current = TaskPhase(self.status)
        transition(self.id, current, target)
        self.status = target.value

    @property
    def is_resolved(self) -> bool:
        return TaskPhase(self.status) in RESOLVED

    @property
    def is_done_executing(self) -> bool:
        return TaskPhase(self.status) in DONE_EXECUTING

    @property
    def unblocks_dependents(self) -> bool:
        return TaskPhase(self.status) in UNBLOCKS_DEPENDENTS

    @property
    def is_ceo_node(self) -> bool:
        return self.node_type in ("ceo_prompt", "ceo_followup", "ceo_request")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "children_ids": list(self.children_ids),
            "employee_id": self.employee_id,
            "description_preview": self._description_preview,
            "acceptance_criteria": list(self.acceptance_criteria),
            "node_type": self.node_type,
            "model_used": self.model_used,
            "project_dir": self.project_dir,
            "status": self.status,
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
            "hold_reason": self.hold_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TaskNode:
        # Extract content fields before filtering to dataclass fields
        has_description = "description" in d
        has_result = "result" in d
        old_format = has_description or has_result
        desc_value = d.get("description", "")
        result_value = d.get("result", "")
        preview_value = d.get("description_preview", "")

        _skip = {"description_preview"}
        filtered = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k not in _skip}
        if "status" in filtered:
            filtered["status"] = _STATUS_MIGRATION.get(filtered["status"], filtered["status"])

        if old_format:
            # Old format: description/result inline — set them on the node
            filtered["description"] = desc_value
            filtered["result"] = result_value
            node = cls(**filtered)
            node._content_dirty = True
            node._content_loaded = True
        else:
            # New format: skeleton only, content loaded lazily
            node = cls(**filtered)
            node._content_dirty = False
            object.__setattr__(node, "_description_preview", preview_value)
        return node


class TaskTree:
    """In-memory task tree with YAML persistence."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.root_id: str = ""
        self._nodes: dict[str, TaskNode] = {}
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
        depends_on: list[str] | None = None,
    ) -> TaskNode:
        parent = self._nodes[parent_id]
        child = TaskNode(
            parent_id=parent_id,
            employee_id=employee_id,
            description=description,
            acceptance_criteria=acceptance_criteria,
            project_id=self.project_id,
            timeout_seconds=timeout_seconds,
            depends_on=depends_on or [],
        )
        parent.children_ids.append(child.id)
        self._nodes[child.id] = child
        return child

    def all_nodes(self) -> list[TaskNode]:
        """Return all nodes in the tree."""
        return list(self._nodes.values())

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

    def all_children_done(self, node_id: str) -> bool:
        """All active children have finished executing (DONE_EXECUTING set)."""
        children = self.get_active_children(node_id)
        if not children:
            return True
        return all(c.is_done_executing for c in children)


    def has_failed_children(self, node_id: str) -> bool:
        return any(c.status == "failed" for c in self.get_active_children(node_id))

    def find_dependents(self, node_id: str) -> list[TaskNode]:
        """Find all nodes that depend on the given node."""
        return [n for n in self._nodes.values() if node_id in n.depends_on]

    def all_deps_resolved(self, node_id: str) -> bool:
        """All depends_on nodes are resolved (RESOLVED set)."""
        node = self._nodes.get(node_id)
        if not node or not node.depends_on:
            return True
        for dep_id in node.depends_on:
            dep = self._nodes.get(dep_id)
            if not dep or not dep.is_resolved:
                return False
        return True


    def is_subtree_resolved(self, node_id: str) -> bool:
        """Check if node AND all descendants are in RESOLVED state.

        Bottom-up semantic: a subtree is resolved when the node itself
        is resolved and every child subtree is also resolved.
        """
        node = self._nodes.get(node_id)
        if not node:
            return False
        if not node.is_resolved:
            return False
        return all(
            self.is_subtree_resolved(cid)
            for cid in node.children_ids
            if cid in self._nodes
        )

    def is_project_complete(self) -> bool:
        """Check if the project is fully complete — ready for retrospective.

        Condition: EA anchor has finished executing (DONE_EXECUTING) and
        every child subtree of the EA anchor is fully resolved (RESOLVED).
        The EA anchor itself may still be COMPLETED (not yet ACCEPTED)
        because acceptance happens as part of the project completion flow.
        """
        ea = self.get_ea_node()
        if not ea:
            return False
        if not ea.is_done_executing:
            return False
        # All children subtrees must be fully resolved
        return all(
            self.is_subtree_resolved(cid)
            for cid in ea.children_ids
            if cid in self._nodes
        )

    def has_failed_deps(self, node_id: str) -> bool:
        """Check if any depends_on node will not deliver (failed/blocked/cancelled)."""
        node = self._nodes.get(node_id)
        if not node:
            return False
        for dep_id in node.depends_on:
            dep = self._nodes.get(dep_id)
            if dep and TaskPhase(dep.status) in WILL_NOT_DELIVER:
                return True
        return False

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Snapshot nodes to avoid "dictionary changed size during iteration"
        # when async save runs concurrently with add_child modifications
        nodes_snapshot = list(self._nodes.values())
        # Externalize dirty node content before writing skeleton
        for node in nodes_snapshot:
            node.save_content(path.parent)
        data = {
            "project_id": self.project_id,
            "root_id": self.root_id,
            "current_branch": self.current_branch,
            "nodes": [n.to_dict() for n in nodes_snapshot],
        }
        path.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path, project_id: str = "", *, skeleton_only: bool = True) -> TaskTree:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        tree = cls(project_id=project_id or data.get("project_id", ""))
        tree.root_id = data.get("root_id", "")
        tree.current_branch = data.get("current_branch", 0)
        tree._source_dir = path.parent
        for nd in data.get("nodes", []):
            node = TaskNode.from_dict(nd)
            tree._nodes[node.id] = node
        # task_id_map removed — ignored for backward compat with old tree files
        if not skeleton_only:
            tree.load_all_content()
        return tree

    def load_all_content(self, project_dir: Path | None = None) -> None:
        """Load content for all nodes from their content files."""
        pdir = project_dir or getattr(self, "_source_dir", None)
        if not pdir:
            return
        for node in self._nodes.values():
            node.load_content(pdir)


# ---------------------------------------------------------------------------
# Tree Registry — in-memory cache + async persistence
# ---------------------------------------------------------------------------

_cache: dict[str, TaskTree] = {}
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()  # protects _locks dict itself


def _key(path: str | Path) -> str:
    return str(Path(path).resolve())


def get_tree(path: str | Path, project_id: str = "") -> TaskTree:
    """Get tree from memory cache, loading from disk if not cached."""
    key = _key(path)
    if key not in _cache:
        _cache[key] = TaskTree.load(Path(path), project_id=project_id)
    return _cache[key]


def register_tree(path: str | Path, tree: TaskTree) -> None:
    """Register a newly created tree in the cache."""
    _cache[_key(path)] = tree


def get_tree_lock(path: str | Path) -> threading.RLock:
    """Get per-tree RLock for protecting read-modify-write sequences.

    Uses threading.RLock (not asyncio.Lock) so it works in both sync
    (LangChain tool threads) and async contexts.  RLock is reentrant,
    so nested calls (e.g. dispatch_child → _save_tree) won't deadlock.
    """
    key = _key(path)
    with _locks_guard:
        if key not in _locks:
            _locks[key] = threading.RLock()
        return _locks[key]


def save_tree_async(path: str | Path) -> None:
    """Schedule async disk save of the cached tree.

    Safe to call from both sync and async contexts.
    If no event loop is running, saves synchronously.
    Acquires the tree lock to prevent concurrent mutation during save.
    """
    key = _key(path)
    tree = _cache.get(key)
    if not tree:
        return
    _path = Path(path)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_save(tree, _path))
    except RuntimeError:
        lock = get_tree_lock(path)
        with lock:
            tree.save(_path)


def evict_tree(path: str | Path) -> None:
    """Remove a tree from the cache (e.g. after project archive)."""
    key = _key(path)
    _cache.pop(key, None)
    with _locks_guard:
        _locks.pop(key, None)


async def _do_save(tree: TaskTree, path: Path) -> None:
    lock = get_tree_lock(path)
    try:
        lock.acquire()
        tree.save(path)
    except Exception as e:
        logger.error("Failed to save tree {}: {}", path, e)
    finally:
        lock.release()
