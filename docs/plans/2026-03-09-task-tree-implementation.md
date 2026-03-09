# Task Tree Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **IMPORTANT:** Read `AI_CONTRIBUTING.md` before writing ANY code. Follow its rules exactly: systematic design, registry pattern, no silent exceptions, TDD, mock at importing module level, loguru logger, lazy imports for circular deps.

**Goal:** Replace the flat dispatch + subtask + acceptance state machine with a unified task tree where EA is root, children are subtrees, and results propagate upward with acceptance at each level.

**Architecture:** TaskNode tree persisted as `task_tree.yaml` per project. New tools (dispatch_child, accept_child, reject_child) replace old dispatch/acceptance tools. Parent nodes are woken when all children complete. LLM interactions logged to `llm_trace.jsonl`.

**Tech Stack:** Python dataclasses, YAML persistence, existing LangChain tool framework, existing EmployeeManager/Launcher infrastructure.

**Key reference files:**
- Design doc: `docs/plans/2026-03-09-task-tree-design.md`
- Coding guide: `AI_CONTRIBUTING.md`
- Current vessel: `src/onemancompany/core/vessel.py` (AgentTask lines 64-116, EmployeeManager lines 446-2125)
- Current tools: `src/onemancompany/agents/common_tools.py` (dispatch_task line 1254, create_subtask line 747, acceptance tools lines 767-895)
- Current archive: `src/onemancompany/core/project_archive.py` (dispatch tracking lines 642-877)

---

### Task 1: Create TaskNode data model and tree persistence

**Files:**
- Create: `src/onemancompany/core/task_tree.py`
- Test: `tests/unit/core/test_task_tree.py`

**Step 1: Write the failing tests**

```python
# tests/unit/core/test_task_tree.py
"""Tests for task tree data model and persistence."""
from __future__ import annotations

import yaml
from pathlib import Path

from onemancompany.core.task_tree import TaskNode, TaskTree


class TestTaskNode:
    def test_create_root_node(self):
        node = TaskNode(employee_id="00001", description="Root task")
        assert node.id  # auto-generated
        assert node.parent_id == ""
        assert node.children_ids == []
        assert node.status == "pending"
        assert node.acceptance_criteria == []
        assert node.created_at  # auto-set

    def test_create_child_node(self):
        node = TaskNode(
            employee_id="00010",
            description="Child task",
            parent_id="root123",
            acceptance_criteria=["Must pass tests"],
        )
        assert node.parent_id == "root123"
        assert node.acceptance_criteria == ["Must pass tests"]

    def test_to_dict_roundtrip(self):
        node = TaskNode(employee_id="00001", description="test")
        d = node.to_dict()
        restored = TaskNode.from_dict(d)
        assert restored.id == node.id
        assert restored.employee_id == node.employee_id
        assert restored.description == node.description


class TestTaskTree:
    def test_create_tree_with_root(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root task")
        assert tree.root_id == root.id
        assert tree.get_node(root.id) is root

    def test_add_child(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        child = tree.add_child(
            parent_id=root.id,
            employee_id="00010",
            description="Child task",
            acceptance_criteria=["Done correctly"],
        )
        assert child.parent_id == root.id
        assert child.id in root.children_ids
        assert child.acceptance_criteria == ["Done correctly"]

    def test_get_children(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "Task A", ["criterion A"])
        c2 = tree.add_child(root.id, "00011", "Task B", ["criterion B"])
        children = tree.get_children(root.id)
        assert len(children) == 2
        assert {c.id for c in children} == {c1.id, c2.id}

    def test_get_siblings(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c2 = tree.add_child(root.id, "00011", "B", [])
        siblings = tree.get_siblings(c1.id)
        assert len(siblings) == 1
        assert siblings[0].id == c2.id

    def test_all_siblings_terminal(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c2 = tree.add_child(root.id, "00011", "B", [])
        c1.status = "accepted"
        c2.status = "accepted"
        assert tree.all_children_terminal(root.id) is True

    def test_not_all_siblings_terminal(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c2 = tree.add_child(root.id, "00011", "B", [])
        c1.status = "accepted"
        c2.status = "processing"
        assert tree.all_children_terminal(root.id) is False

    def test_has_failed_children(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c2 = tree.add_child(root.id, "00011", "B", [])
        c1.status = "completed"
        c2.status = "failed"
        assert tree.has_failed_children(root.id) is True

    def test_save_and_load(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        child = tree.add_child(root.id, "00010", "Child", ["Must work"])
        child.status = "completed"
        child.result = "Done"

        path = tmp_path / "task_tree.yaml"
        tree.save(path)
        assert path.exists()

        loaded = TaskTree.load(path, project_id="proj1")
        assert loaded.root_id == root.id
        assert len(loaded.get_children(root.id)) == 1
        loaded_child = loaded.get_node(child.id)
        assert loaded_child.status == "completed"
        assert loaded_child.result == "Done"
        assert loaded_child.acceptance_criteria == ["Must work"]

    def test_save_creates_parent_dirs(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        tree.create_root(employee_id="00001", description="Root")
        path = tmp_path / "deep" / "nested" / "task_tree.yaml"
        tree.save(path)
        assert path.exists()
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'onemancompany.core.task_tree'`

**Step 3: Implement TaskNode and TaskTree**

```python
# src/onemancompany/core/task_tree.py
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

    status: str = "pending"  # pending → processing → completed → accepted / failed / cancelled
    result: str = ""
    acceptance_result: dict | None = None  # {passed: bool, notes: str}

    project_id: str = ""
    created_at: str = ""
    completed_at: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "children_ids": list(self.children_ids),
            "employee_id": self.employee_id,
            "description": self.description,
            "acceptance_criteria": list(self.acceptance_criteria),
            "status": self.status,
            "result": self.result,
            "acceptance_result": self.acceptance_result,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
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

    def create_root(self, employee_id: str, description: str) -> TaskNode:
        node = TaskNode(employee_id=employee_id, description=description, project_id=self.project_id)
        self.root_id = node.id
        self._nodes[node.id] = node
        return node

    def add_child(
        self,
        parent_id: str,
        employee_id: str,
        description: str,
        acceptance_criteria: list[str],
    ) -> TaskNode:
        parent = self._nodes[parent_id]
        child = TaskNode(
            parent_id=parent_id,
            employee_id=employee_id,
            description=description,
            acceptance_criteria=acceptance_criteria,
            project_id=self.project_id,
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
        return [self._nodes[cid] for cid in parent.children_ids if cid != node_id and cid in self._nodes]

    def all_children_terminal(self, node_id: str) -> bool:
        children = self.get_children(node_id)
        if not children:
            return True
        return all(c.is_terminal for c in children)

    def has_failed_children(self, node_id: str) -> bool:
        return any(c.status == "failed" for c in self.get_children(node_id))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "project_id": self.project_id,
            "root_id": self.root_id,
            "nodes": [n.to_dict() for n in self._nodes.values()],
        }
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, project_id: str = "") -> TaskTree:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        tree = cls(project_id=project_id or data.get("project_id", ""))
        tree.root_id = data.get("root_id", "")
        for nd in data.get("nodes", []):
            node = TaskNode.from_dict(nd)
            tree._nodes[node.id] = node
        return tree
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/task_tree.py tests/unit/core/test_task_tree.py
git commit -m "feat: add TaskNode and TaskTree data model with persistence"
```

---

### Task 2: Create LLM trace recorder

**Files:**
- Create: `src/onemancompany/core/llm_trace.py`
- Test: `tests/unit/core/test_llm_trace.py`

**Step 1: Write the failing tests**

```python
# tests/unit/core/test_llm_trace.py
"""Tests for LLM interaction trace logger."""
from __future__ import annotations

import json
from pathlib import Path

from onemancompany.core.llm_trace import LlmTracer


class TestLlmTracer:
    def test_log_prompt(self, tmp_path):
        path = tmp_path / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_prompt("node1", "00003", "You are a COO...")

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["node_id"] == "node1"
        assert record["employee_id"] == "00003"
        assert record["type"] == "prompt"
        assert record["content"] == "You are a COO..."
        assert "ts" in record

    def test_log_response(self, tmp_path):
        path = tmp_path / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_response("node1", "00003", "I'll dispatch...", model="claude-sonnet-4-6", input_tokens=100, output_tokens=50)

        record = json.loads(path.read_text().strip())
        assert record["type"] == "response"
        assert record["model"] == "claude-sonnet-4-6"
        assert record["input_tokens"] == 100
        assert record["output_tokens"] == 50

    def test_log_tool_call(self, tmp_path):
        path = tmp_path / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_tool_call("node1", "00003", "dispatch_child", {"employee_id": "00010", "description": "Build API"})

        record = json.loads(path.read_text().strip())
        assert record["type"] == "tool_call"
        assert record["content"]["tool"] == "dispatch_child"
        assert record["content"]["args"]["employee_id"] == "00010"

    def test_log_tool_result(self, tmp_path):
        path = tmp_path / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_tool_result("node1", "00003", {"status": "ok", "node_id": "child1"})

        record = json.loads(path.read_text().strip())
        assert record["type"] == "tool_result"
        assert record["content"]["status"] == "ok"

    def test_multiple_entries_appended(self, tmp_path):
        path = tmp_path / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_prompt("n1", "00003", "prompt1")
        tracer.log_response("n1", "00003", "response1")
        tracer.log_prompt("n2", "00010", "prompt2")

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_prompt("n1", "00003", "test")
        assert path.exists()
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_llm_trace.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement LlmTracer**

```python
# src/onemancompany/core/llm_trace.py
"""LLM interaction trace logger.

Appends one JSON line per interaction to {project_dir}/llm_trace.jsonl.
Records prompts, responses, tool calls, and tool results for each TaskNode.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class LlmTracer:
    """Append-only JSONL logger for LLM interactions."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _append(self, record: dict) -> None:
        record["ts"] = datetime.now().isoformat()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_prompt(self, node_id: str, employee_id: str, content: str) -> None:
        self._append({"node_id": node_id, "employee_id": employee_id, "type": "prompt", "content": content})

    def log_response(
        self, node_id: str, employee_id: str, content: str,
        *, model: str = "", input_tokens: int = 0, output_tokens: int = 0,
    ) -> None:
        self._append({
            "node_id": node_id, "employee_id": employee_id, "type": "response",
            "content": content, "model": model,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
        })

    def log_tool_call(self, node_id: str, employee_id: str, tool_name: str, args: dict) -> None:
        self._append({
            "node_id": node_id, "employee_id": employee_id, "type": "tool_call",
            "content": {"tool": tool_name, "args": args},
        })

    def log_tool_result(self, node_id: str, employee_id: str, result: dict) -> None:
        self._append({
            "node_id": node_id, "employee_id": employee_id, "type": "tool_result",
            "content": result,
        })
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_llm_trace.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/llm_trace.py tests/unit/core/test_llm_trace.py
git commit -m "feat: add LLM trace recorder for per-project interaction logging"
```

---

### Task 3: Create new task tree tools (dispatch_child, accept_child, reject_child)

**Files:**
- Create: `src/onemancompany/agents/tree_tools.py`
- Test: `tests/unit/agents/test_tree_tools.py`

**Context:** These tools replace `dispatch_task`, `dispatch_team_tasks`, `create_subtask`, `set_acceptance_criteria`, `accept_project`, `ea_review_project`. They operate on the TaskTree and use the existing `_current_vessel` / `_current_task_id` context variables to find the current node.

**Step 1: Write the failing tests**

```python
# tests/unit/agents/test_tree_tools.py
"""Tests for task tree tools — dispatch_child, accept_child, reject_child."""
from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from onemancompany.core.task_tree import TaskNode, TaskTree


class TestDispatchChild:
    def test_dispatches_child_node(self, tmp_path):
        """dispatch_child creates a child node in the tree and pushes task to target employee."""
        from onemancompany.agents.tree_tools import dispatch_child
        from onemancompany.core import state as state_mod
        from onemancompany.core.vessel import _current_vessel, _current_task_id

        # Setup: tree with root node
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        root.status = "processing"
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        # Mock vessel and context
        mock_vessel = MagicMock()
        mock_vessel.board = MagicMock()
        mock_task = MagicMock()
        mock_task.id = "task1"
        mock_task.project_id = "proj1"
        mock_task.project_dir = str(tmp_path)
        mock_vessel.board.get_task.return_value = mock_task

        # Mock employee manager
        mock_em = MagicMock()
        mock_handle = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        # Mock company state with target employee
        cs = MagicMock()
        cs.employees = {"00010": MagicMock(id="00010", name="Alice")}

        token1 = _current_vessel.set(mock_vessel)
        token2 = _current_task_id.set("task1")
        try:
            with patch("onemancompany.agents.tree_tools.company_state", cs), \
                 patch("onemancompany.agents.tree_tools.employee_manager", mock_em), \
                 patch("onemancompany.agents.tree_tools._load_tree", return_value=tree), \
                 patch("onemancompany.agents.tree_tools._save_tree") as mock_save, \
                 patch("onemancompany.agents.tree_tools._get_current_node_id", return_value=root.id):
                result = dispatch_child.invoke({
                    "employee_id": "00010",
                    "description": "Build API",
                    "acceptance_criteria": ["API responds 200"],
                })
        finally:
            _current_vessel.reset(token1)
            _current_task_id.reset(token2)

        assert result["status"] == "dispatched"
        assert result["node_id"]
        # Child should be in tree
        child = tree.get_node(result["node_id"])
        assert child is not None
        assert child.parent_id == root.id
        assert child.employee_id == "00010"
        assert child.acceptance_criteria == ["API responds 200"]
        # Should have pushed task to target employee
        mock_handle.push_task.assert_called_once()
        # Tree should be saved
        mock_save.assert_called_once()

    def test_rejects_unknown_employee(self):
        """dispatch_child returns error for non-existent employee."""
        from onemancompany.agents.tree_tools import dispatch_child
        from onemancompany.core.vessel import _current_vessel, _current_task_id

        mock_vessel = MagicMock()
        mock_task = MagicMock()
        mock_task.id = "task1"
        mock_task.project_id = "proj1"
        mock_task.project_dir = "/tmp"
        mock_vessel.board.get_task.return_value = mock_task

        cs = MagicMock()
        cs.employees = {}

        token1 = _current_vessel.set(mock_vessel)
        token2 = _current_task_id.set("task1")
        try:
            with patch("onemancompany.agents.tree_tools.company_state", cs):
                result = dispatch_child.invoke({
                    "employee_id": "99999",
                    "description": "Build API",
                    "acceptance_criteria": ["Done"],
                })
        finally:
            _current_vessel.reset(token1)
            _current_task_id.reset(token2)

        assert result["status"] == "error"


class TestAcceptChild:
    def test_accepts_completed_child(self, tmp_path):
        """accept_child marks a child node as accepted."""
        from onemancompany.agents.tree_tools import accept_child
        from onemancompany.core.vessel import _current_vessel, _current_task_id

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        child = tree.add_child(root.id, "00010", "Build API", ["Done"])
        child.status = "completed"
        child.result = "API built"

        mock_vessel = MagicMock()
        mock_task = MagicMock()
        mock_task.id = "task1"
        mock_task.project_id = "proj1"
        mock_task.project_dir = str(tmp_path)
        mock_vessel.board.get_task.return_value = mock_task

        token1 = _current_vessel.set(mock_vessel)
        token2 = _current_task_id.set("task1")
        try:
            with patch("onemancompany.agents.tree_tools._load_tree", return_value=tree), \
                 patch("onemancompany.agents.tree_tools._save_tree"):
                result = accept_child.invoke({"node_id": child.id, "notes": "Looks good"})
        finally:
            _current_vessel.reset(token1)
            _current_task_id.reset(token2)

        assert result["status"] == "accepted"
        assert child.status == "accepted"
        assert child.acceptance_result["passed"] is True


class TestRejectChild:
    def test_rejects_with_retry(self, tmp_path):
        """reject_child with retry=True pushes correction task to same employee."""
        from onemancompany.agents.tree_tools import reject_child
        from onemancompany.core.vessel import _current_vessel, _current_task_id

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        child = tree.add_child(root.id, "00010", "Build API", ["Must handle errors"])
        child.status = "completed"
        child.result = "API built but no error handling"

        mock_vessel = MagicMock()
        mock_task = MagicMock()
        mock_task.id = "task1"
        mock_task.project_id = "proj1"
        mock_task.project_dir = str(tmp_path)
        mock_vessel.board.get_task.return_value = mock_task

        mock_em = MagicMock()
        mock_handle = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        token1 = _current_vessel.set(mock_vessel)
        token2 = _current_task_id.set("task1")
        try:
            with patch("onemancompany.agents.tree_tools._load_tree", return_value=tree), \
                 patch("onemancompany.agents.tree_tools._save_tree"), \
                 patch("onemancompany.agents.tree_tools.employee_manager", mock_em):
                result = reject_child.invoke({
                    "node_id": child.id,
                    "reason": "Missing error handling",
                    "retry": True,
                })
        finally:
            _current_vessel.reset(token1)
            _current_task_id.reset(token2)

        assert result["status"] == "rejected_retry"
        # Should push correction task
        mock_handle.push_task.assert_called_once()
        # Child status should be reset to pending for retry
        assert child.status == "pending"

    def test_rejects_without_retry(self, tmp_path):
        """reject_child with retry=False marks child as failed."""
        from onemancompany.agents.tree_tools import reject_child
        from onemancompany.core.vessel import _current_vessel, _current_task_id

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        child = tree.add_child(root.id, "00010", "Build API", ["Must work"])
        child.status = "completed"

        mock_vessel = MagicMock()
        mock_task = MagicMock()
        mock_task.id = "task1"
        mock_task.project_id = "proj1"
        mock_task.project_dir = str(tmp_path)
        mock_vessel.board.get_task.return_value = mock_task

        token1 = _current_vessel.set(mock_vessel)
        token2 = _current_task_id.set("task1")
        try:
            with patch("onemancompany.agents.tree_tools._load_tree", return_value=tree), \
                 patch("onemancompany.agents.tree_tools._save_tree"):
                result = reject_child.invoke({
                    "node_id": child.id,
                    "reason": "Cannot be fixed",
                    "retry": False,
                })
        finally:
            _current_vessel.reset(token1)
            _current_task_id.reset(token2)

        assert result["status"] == "rejected_failed"
        assert child.status == "failed"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement tree tools**

Create `src/onemancompany/agents/tree_tools.py` with:
- `_load_tree(project_dir)` / `_save_tree(project_dir, tree)` — helpers to load/save from `{project_dir}/task_tree.yaml`
- `_get_current_node_id()` — maps current `_current_task_id` to a TaskNode id (uses a module-level dict `_task_to_node: dict[str, str]`)
- `dispatch_child(employee_id, description, acceptance_criteria)` — `@tool` decorated, creates child node, pushes task to target employee via `employee_manager.get_handle(employee_id).push_task()`
- `accept_child(node_id, notes)` — `@tool` decorated, marks node accepted
- `reject_child(node_id, reason, retry)` — `@tool` decorated, marks failed or resets to pending + pushes correction task

Use lazy imports for `company_state`, `employee_manager` to avoid circular deps. Register all 3 tools into `tool_registry` with `category="base"` at the bottom of the file.

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/agents/tree_tools.py tests/unit/agents/test_tree_tools.py
git commit -m "feat: add dispatch_child, accept_child, reject_child tree tools"
```

---

### Task 4: Wire task execution to TaskTree — child completion callback

**Files:**
- Modify: `src/onemancompany/core/vessel.py`
- Modify: `tests/unit/core/test_agent_loop.py`

**Context:** When a leaf node (employee task) completes, the system must:
1. Update the TaskNode status + result in task_tree.yaml
2. Check if all siblings are terminal
3. If yes: wake the parent employee with a review prompt

This replaces the old `_post_task_cleanup` → `_dispatch_ready_subtasks` → phase state machine.

**Step 1: Write the failing tests**

Add to `tests/unit/core/test_agent_loop.py`:

```python
class TestTaskTreeCallback:
    """Tests for task tree child-completion callback in EmployeeManager."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_child_complete_wakes_parent_when_all_siblings_done(self, mock_bus, mock_state):
        """When last sibling completes, parent employee gets a review task."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        parent_launcher = MagicMock(spec=Launcher)
        mgr.register("00003", parent_launcher)

        # Setup tree: root(00001) -> parent(00003) -> [child1(00010), child2(00011)]
        from onemancompany.core.task_tree import TaskTree
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        parent_node = tree.add_child(root.id, "00003", "Manage feature", ["Feature works"])
        child1 = tree.add_child(parent_node.id, "00010", "Backend", ["API done"])
        child2 = tree.add_child(parent_node.id, "00011", "Frontend", ["UI done"])
        child1.status = "accepted"  # Already done
        child2.status = "completed"  # Just completed
        child2.result = "Frontend built"

        task = AgentTask(id="t1", description="Frontend", project_id="proj1", project_dir="/tmp/proj", result="Frontend built")

        with patch("onemancompany.core.vessel._load_project_tree", return_value=tree), \
             patch("onemancompany.core.vessel._save_project_tree"), \
             patch("onemancompany.core.vessel._node_id_for_task", return_value=child2.id):
            await mgr._on_child_complete("00011", task, project_id="proj1")

        # Parent (00003) should have received a review task
        assert mgr.boards["00003"].get_next_pending() is not None

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_child_complete_waits_when_siblings_pending(self, mock_bus, mock_state):
        """When siblings still running, no wake-up."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        from onemancompany.core.task_tree import TaskTree
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        parent_node = tree.add_child(root.id, "00003", "Manage", [])
        child1 = tree.add_child(parent_node.id, "00010", "Backend", [])
        child2 = tree.add_child(parent_node.id, "00011", "Frontend", [])
        child1.status = "completed"
        child2.status = "processing"  # Still running

        task = AgentTask(id="t1", description="Backend", project_id="proj1", project_dir="/tmp/proj", result="Done")

        with patch("onemancompany.core.vessel._load_project_tree", return_value=tree), \
             patch("onemancompany.core.vessel._save_project_tree"), \
             patch("onemancompany.core.vessel._node_id_for_task", return_value=child1.id):
            await mgr._on_child_complete("00010", task, project_id="proj1")

        # Parent should NOT be woken
        assert "00003" not in mgr.boards or mgr.boards["00003"].get_next_pending() is None
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_agent_loop.py::TestTaskTreeCallback -v`
Expected: FAIL (methods don't exist yet)

**Step 3: Implement in vessel.py**

Add to `EmployeeManager`:
- `_on_child_complete(employee_id, task, project_id)` — called after a task finishes execution:
  1. Load tree from `{task.project_dir}/task_tree.yaml`
  2. Find the TaskNode for this task (via `_node_id_for_task` mapping)
  3. Update node: status="completed", result=task.result, tokens, cost
  4. Save tree
  5. Check `tree.all_children_terminal(node.parent_id)`
  6. If yes: build review prompt with all child results, push to parent employee's board

- Replace the call to `_post_task_cleanup` at line 942 of `_execute_task` with a call to `_on_child_complete`

Add module-level helpers:
- `_load_project_tree(project_dir) -> TaskTree`
- `_save_project_tree(project_dir, tree)`
- `_node_id_for_task(task_id) -> str` — lookup from a module-level dict that `dispatch_child` populates

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_agent_loop.py::TestTaskTreeCallback -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_agent_loop.py
git commit -m "feat: add child-completion callback with parent wake-up"
```

---

### Task 5: Wire EA as root node — project creation creates tree

**Files:**
- Modify: `src/onemancompany/agents/ea_agent.py`
- Modify: `src/onemancompany/core/vessel.py` (project creation path)
- Modify: `tests/unit/agents/test_ea_agent.py`

**Context:** When CEO gives a task, the system creates a project and a TaskTree with EA as root. EA's system prompt must be updated to use `dispatch_child` instead of `dispatch_task`.

**Step 1: Update EA system prompt**

Replace the EA_SYSTEM_PROMPT in `ea_agent.py` to:
- Reference `dispatch_child(employee_id, description, acceptance_criteria)` instead of `dispatch_task`
- Remove references to `set_acceptance_criteria`, `set_project_budget`, `accept_project`, `ea_review_project`
- Add instructions for reviewing child results: use `accept_child()` / `reject_child()`
- Explain that when woken for review, EA should check results and either accept, reject, or dispatch more children
- When all work is done, call `report_to_ceo()` with final summary

**Step 2: Modify project creation to initialize TaskTree**

In `vessel.py`, when a task is first assigned to EA (root task creation), also:
1. Create `task_tree.yaml` in the project directory
2. Set EA as root node
3. Map the EA's AgentTask ID to the root TaskNode ID

**Step 3: Write tests**

Test that:
- New project creates task_tree.yaml with EA root
- EA system prompt contains `dispatch_child` and not `dispatch_task`

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_ea_agent.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/agents/ea_agent.py src/onemancompany/core/vessel.py tests/unit/agents/test_ea_agent.py
git commit -m "feat: wire EA as task tree root, update system prompt for tree tools"
```

---

### Task 6: Delete old dispatch/acceptance tools and related code

**Files:**
- Modify: `src/onemancompany/agents/common_tools.py` — delete `dispatch_task`, `dispatch_team_tasks`, `create_subtask`, `set_acceptance_criteria`, `accept_project`, `ea_review_project`, `save_project_plan`, and their registration in `_register_all_internal_tools()`
- Modify: `src/onemancompany/core/project_archive.py` — delete `record_dispatch`, `record_dispatch_completion`, `record_dispatch_failure`, `all_dispatches_complete`, `get_ready_dispatches`, `activate_dispatch`, `record_team_dispatches`, `set_acceptance_result`, `set_ea_review_result`, `find_duplicate_dispatch`
- Modify: `src/onemancompany/core/vessel.py` — delete `_post_task_cleanup`, `_determine_project_phase`, all phase handlers (`_handle_completed`, `_handle_needs_acceptance`, `_handle_rejected_by_coo`, `_handle_accepted`, `_handle_ea_approved`, `_handle_ea_rejected`), `_dispatch_ready_subtasks`, `_push_acceptance_task`, `_push_ea_review_task`, `_push_rectification_task`, `_execute_subtask`, `_completion_check`, all `_PHASE_*` constants
- Modify: `src/onemancompany/core/vessel.py` — delete `AgentTask.sub_task_ids`, `AgentTask.depends_on`, `AgentTask.original_project_id`, `AgentTask.original_project_dir`, `AgentTask.task_type`, `AgentTaskBoard.get_pending_subtasks`
- Modify: `src/onemancompany/agents/coo_agent.py` — update system prompt to use `dispatch_child` instead of `dispatch_task` / `dispatch_team_tasks`
- Modify: `src/onemancompany/agents/cso_agent.py` — update system prompt if it references old tools
- Modify: `src/onemancompany/agents/base.py` — remove any references to old tools
- Modify: `src/onemancompany/core/task_lifecycle.py` — simplify TaskPhase enum (remove NEEDS_ACCEPTANCE, ACCEPTED, REJECTED, RECTIFICATION, REVIEWING), update TASK_LIFECYCLE_DOC
- Modify: `src/onemancompany/core/routine.py` — remove references to old dispatch functions
- Modify: `src/onemancompany/api/routes.py` — update any endpoints referencing old tools/dispatch functions

**Step 1: Delete old tools from common_tools.py**

Remove the following functions and their `@tool` decorators:
- `dispatch_task` (lines 1253-1346)
- `dispatch_team_tasks` (lines 1350-1488)
- `create_subtask` (lines 746-764)
- `set_acceptance_criteria` (lines 767-809)
- `accept_project` (lines 812-849)
- `ea_review_project` (lines 852-895)
- `save_project_plan` (lines 926-1158)

Update `_register_all_internal_tools()` to:
- Remove deleted tools from `_base` and `_gated` lists
- Import and register tree_tools: `from onemancompany.agents import tree_tools as _tt  # noqa: F401` (tree_tools self-registers)

**Step 2: Delete dispatch tracking from project_archive.py**

Remove functions:
- `record_dispatch` (lines 642-685)
- `record_dispatch_completion` (lines 725-752)
- `record_dispatch_failure` (lines 755-816)
- `all_dispatches_complete` (lines 819-828)
- `record_team_dispatches` (lines 831-842)
- `get_ready_dispatches` (lines 845-864)
- `activate_dispatch` (lines 867-877)
- `set_acceptance_result` (lines 880-891)
- `set_ea_review_result` (lines 894-906)
- `find_duplicate_dispatch` (wherever defined)

**Step 3: Delete old vessel.py code**

Remove:
- All `_PHASE_*` constants (lines 1383-1388)
- `_determine_project_phase` (lines 1390-1424)
- `_post_task_cleanup` (lines 1426-1477)
- All phase handlers (lines 1478-1619)
- `_push_acceptance_task` (lines 1900-1951)
- `_push_ea_review_task` (lines 1953-2008)
- `_push_rectification_task` (lines 2010-2064)
- `_dispatch_ready_subtasks` (lines 1341-1374)
- `_execute_subtask` (lines 948-1030)
- `_completion_check` (lines 1035-1081)
- Sub-task loop in `_execute_task` (lines 866-885)
- `AgentTask` fields: `sub_task_ids`, `depends_on`, `original_project_id`, `original_project_dir`, `task_type`
- `AgentTaskBoard.get_pending_subtasks`

**Step 4: Update agent system prompts**

- `coo_agent.py`: Replace `dispatch_task` / `dispatch_team_tasks` references with `dispatch_child`
- `cso_agent.py`: Same if applicable
- `task_lifecycle.py`: Remove project sub-states from TaskPhase, simplify TASK_LIFECYCLE_DOC

**Step 5: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`
Expected: `OK`

**Step 6: Fix broken tests**

Run: `.venv/bin/python -m pytest tests/unit/ -x`

Fix all test failures caused by deleted functions/tools. This includes:
- `tests/unit/agents/test_common_tools.py` — delete tests for removed tools
- `tests/unit/core/test_agent_loop.py` — delete tests for old post_task_cleanup/phase handlers, subtask execution
- `tests/unit/core/test_project_archive.py` — delete tests for removed dispatch functions
- `tests/unit/agents/test_ea_agent.py` — update for new prompt

**Step 7: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: ALL PASS

**Step 8: Commit**

```bash
git add -A
git commit -m "refactor: delete old dispatch/acceptance/subtask system, replaced by task tree"
```

---

### Task 7: Integration — wire _full_cleanup to task tree root completion

**Files:**
- Modify: `src/onemancompany/core/vessel.py`
- Modify: `tests/unit/core/test_agent_loop.py`

**Context:** When EA's root node has all children accepted and EA calls `report_to_ceo()`, the project should be cleaned up. The `_full_cleanup` method (reset employee statuses, complete project, publish events) should be triggered when the root node is marked complete.

**Step 1: Implement root completion detection**

In `_on_child_complete`: when the node being completed IS the root (no parent_id), trigger `_full_cleanup` instead of waking a parent.

Also: when EA accepts all children and doesn't dispatch more (task finishes naturally), mark root node as accepted and run cleanup.

**Step 2: Write tests**

Test that:
- Root node completion triggers `_full_cleanup`
- `agent_done` event is published with summary
- Employee statuses are reset
- Project is marked complete

**Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_agent_loop.py -v`
Expected: ALL PASS

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_agent_loop.py
git commit -m "feat: wire root node completion to project cleanup and CEO notification"
```

---

### Task 8: Register tree tools in tool_registry and MCP server

**Files:**
- Modify: `src/onemancompany/tools/mcp/server.py` — add `tree_tools` import to trigger registration
- Modify: `src/onemancompany/agents/tree_tools.py` — ensure tools are registered in tool_registry
- Test: verify via `.venv/bin/python -c` that tools appear in registry

**Step 1: Verify tree tools register into tool_registry**

Ensure `tree_tools.py` has at bottom:

```python
from onemancompany.core.tool_registry import ToolMeta, tool_registry

tool_registry.register(dispatch_child, ToolMeta(name="dispatch_child", category="base"))
tool_registry.register(accept_child, ToolMeta(name="accept_child", category="base"))
tool_registry.register(reject_child, ToolMeta(name="reject_child", category="base"))
```

**Step 2: Update MCP server imports**

In `src/onemancompany/tools/mcp/server.py`, add to `main()`:
```python
from onemancompany.agents import tree_tools as _  # noqa: F401
```

**Step 3: Verify**

Run:
```bash
.venv/bin/python -c "
from onemancompany.agents import common_tools, tree_tools, coo_agent, hr_agent
from onemancompany.core.tool_registry import tool_registry
names = tool_registry.all_tool_names()
assert 'dispatch_child' in names
assert 'accept_child' in names
assert 'reject_child' in names
assert 'dispatch_task' not in names
assert 'create_subtask' not in names
print(f'OK — {len(names)} tools, tree tools present, old tools removed')
"
```
Expected: `OK — N tools, tree tools present, old tools removed`

**Step 4: Commit**

```bash
git add src/onemancompany/agents/tree_tools.py src/onemancompany/tools/mcp/server.py
git commit -m "feat: register tree tools in tool_registry and MCP server"
```

---

### Task 9: Final integration test and cleanup

**Files:**
- All modified files
- Test: full test suite

**Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: ALL PASS, 0 failures

**Step 2: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`
Expected: `OK`

**Step 3: Verify no references to deleted functions remain**

Run:
```bash
grep -r "dispatch_task\|dispatch_team_tasks\|create_subtask\|set_acceptance_criteria\|accept_project\|ea_review_project\|save_project_plan\|record_dispatch\|all_dispatches_complete\|_push_acceptance_task\|_push_ea_review_task\|_push_rectification_task" src/ --include="*.py" -l
```
Expected: No results (or only comments/docstrings)

**Step 4: Verify no code quality issues**

Run: `.venv/bin/python -m pytest tests/unit/test_code_quality.py -v`
Expected: ALL PASS

**Step 5: Commit any final fixes**

```bash
git add -A
git commit -m "chore: final cleanup — remove all references to old dispatch system"
```
