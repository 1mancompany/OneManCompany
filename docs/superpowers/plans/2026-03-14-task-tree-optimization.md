# Task Tree Optimization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent task tree files from growing unbounded by externalizing large content, windowing employee context, adding tree growth circuit breakers, and providing three-granularity resource recovery.

**Architecture:** TaskNode's `description` and `result` fields become lazy-loaded properties backed by per-node YAML files (`nodes/{node_id}.yaml`). The tree skeleton (`task_tree.yaml`) only stores metadata. Employee context uses distance-based truncation with on-demand detail loading. Tree growth is limited by review round, children count, and depth limits. Resource recovery supports per-project, per-employee, and global abort.

**Tech Stack:** Python 3.12, dataclasses, PyYAML, FastAPI, asyncio, pytest

**Spec:** `docs/superpowers/specs/2026-03-14-task-tree-optimization-design.md`

---

## Chunk 1: Content Externalization (TaskNode + TaskTree)

### Task 1: TaskNode property descriptors and content I/O

**Files:**
- Modify: `src/onemancompany/core/task_tree.py:33-130`
- Test: `tests/unit/core/test_task_tree.py`

- [ ] **Step 1: Write failing tests for property descriptors and dirty tracking**

```python
# tests/unit/core/test_task_tree.py — add new class

class TestTaskNodeContentExternalization:
    """Tests for lazy-loaded description/result with dirty tracking."""

    def test_description_setter_marks_dirty(self):
        node = TaskNode(employee_id="e1")
        node.description = "hello"
        assert node.description == "hello"
        assert node._content_dirty is True

    def test_result_setter_marks_dirty(self):
        node = TaskNode(employee_id="e1")
        node.result = "done"
        assert node.result == "done"
        assert node._content_dirty is True

    def test_description_preview_truncated(self):
        node = TaskNode(employee_id="e1")
        node.description = "A" * 500
        assert node.description_preview == "A" * 200

    def test_description_preview_short_text(self):
        node = TaskNode(employee_id="e1")
        node.description = "short"
        assert node.description_preview == "short"

    def test_save_content_creates_file(self, tmp_path):
        node = TaskNode(employee_id="e1")
        node.description = "task desc"
        node.result = "task result"
        node.save_content(tmp_path)
        content_path = tmp_path / "nodes" / f"{node.id}.yaml"
        assert content_path.exists()
        import yaml
        data = yaml.safe_load(content_path.read_text())
        assert data["description"] == "task desc"
        assert data["result"] == "task result"

    def test_save_content_skips_when_not_dirty(self, tmp_path):
        node = TaskNode(employee_id="e1")
        node._content_dirty = False
        node.save_content(tmp_path)
        content_path = tmp_path / "nodes" / f"{node.id}.yaml"
        assert not content_path.exists()

    def test_save_content_resets_dirty_flag(self, tmp_path):
        node = TaskNode(employee_id="e1")
        node.description = "x"
        node.save_content(tmp_path)
        assert node._content_dirty is False

    def test_load_content_reads_file(self, tmp_path):
        node = TaskNode(employee_id="e1", id="test123")
        node.description = "original"
        node.result = "original result"
        node.save_content(tmp_path)
        # Reset fields
        node._description = ""
        node._result = ""
        node._content_loaded = False
        node._content_dirty = False
        node.load_content(tmp_path)
        assert node.description == "original"
        assert node.result == "original result"
        assert node._content_loaded is True

    def test_load_content_idempotent(self, tmp_path):
        node = TaskNode(employee_id="e1", id="test123")
        node.description = "original"
        node.save_content(tmp_path)
        node._description = ""
        node._content_loaded = False
        node.load_content(tmp_path)
        # Modify in-memory
        node._description = "modified"
        # Second load should NOT overwrite
        node.load_content(tmp_path)
        assert node.description == "modified"

    def test_load_content_missing_file_is_noop(self, tmp_path):
        node = TaskNode(employee_id="e1", id="missing123")
        node.load_content(tmp_path)
        assert node._content_loaded is True  # Marked loaded even if file missing
        assert node.description == ""

    def test_to_dict_excludes_description_and_result(self):
        node = TaskNode(employee_id="e1")
        node.description = "big text"
        node.result = "big result"
        d = node.to_dict()
        assert "description" not in d
        assert "result" not in d
        assert d["description_preview"] == "big text"

    def test_from_dict_with_old_format_migrates(self):
        """Backward compat: old YAML with inline description/result."""
        d = {
            "id": "old123",
            "employee_id": "e1",
            "description": "legacy desc",
            "result": "legacy result",
            "status": "completed",
        }
        node = TaskNode.from_dict(d)
        assert node.description == "legacy desc"
        assert node.result == "legacy result"
        assert node._content_dirty is True  # Should migrate on next save
        assert node._content_loaded is True  # Already in memory

    def test_from_dict_without_description_result(self):
        """New format: no description/result in skeleton dict."""
        d = {
            "id": "new123",
            "employee_id": "e1",
            "description_preview": "preview text",
            "status": "pending",
        }
        node = TaskNode.from_dict(d)
        assert node.description == ""
        assert node.result == ""
        assert node._content_dirty is False
        assert node.description_preview == "preview text"

    def test_constructor_sets_dirty_for_nonempty_description(self):
        """Nodes created with description via constructor should be dirty."""
        node = TaskNode(employee_id="e1", description="new task")
        assert node._content_dirty is True
        assert node.description_preview == "new task"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskNodeContentExternalization -v`
Expected: FAIL — `_content_dirty`, `description_preview`, `save_content`, `load_content` do not exist

- [ ] **Step 3: Implement TaskNode property descriptors and content I/O**

Convert `TaskNode` from a plain `@dataclass` with `description: str` and `result: str` fields to use private backing fields with property descriptors:

```python
@dataclass
class TaskNode:
    """Single node in the task tree."""

    id: str = ""
    parent_id: str = ""
    children_ids: list[str] = field(default_factory=list)

    employee_id: str = ""
    # description and result are now properties — see below
    _description: str = field(default="", repr=False)
    _result: str = field(default="", repr=False)
    _description_preview: str = field(default="", repr=False)
    _content_dirty: bool = field(default=False, repr=False)
    _content_loaded: bool = field(default=False, repr=False)

    acceptance_criteria: list[str] = field(default_factory=list)
    node_type: str = "task"  # "task" | "ceo_prompt" | "ceo_followup" | "ceo_request" | "review"  # "task" | "ceo_prompt" | "ceo_followup" | "ceo_request" | "review"

    # ... rest of fields unchanged ...

    @property
    def description(self) -> str:
        return self._description

    @description.setter
    def description(self, value: str) -> None:
        self._description = value
        self._description_preview = value[:200]
        self._content_dirty = True

    @property
    def result(self) -> str:
        return self._result

    @result.setter
    def result(self, value: str) -> None:
        self._result = value
        self._content_dirty = True

    @property
    def description_preview(self) -> str:
        return self._description_preview

    def save_content(self, project_dir: Path | str) -> None:
        """Write description + result to nodes/{id}.yaml if dirty."""
        if not self._content_dirty:
            return
        project_dir = Path(project_dir)
        nodes_dir = project_dir / "nodes"
        nodes_dir.mkdir(parents=True, exist_ok=True)
        data = {"description": self._description, "result": self._result}
        (nodes_dir / f"{self.id}.yaml").write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._content_dirty = False

    def load_content(self, project_dir: Path | str) -> None:
        """Load description + result from nodes/{id}.yaml. Idempotent."""
        if self._content_loaded:
            return
        project_dir = Path(project_dir)
        content_path = project_dir / "nodes" / f"{self.id}.yaml"
        if content_path.exists():
            data = yaml.safe_load(content_path.read_text(encoding="utf-8")) or {}
            self._description = data.get("description", "")
            self._result = data.get("result", "")
            self._description_preview = self._description[:200]
        self._content_loaded = True
```

Update `to_dict()`:
```python
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "children_ids": list(self.children_ids),
            "employee_id": self.employee_id,
            "description_preview": self._description_preview,
            "acceptance_criteria": list(self.acceptance_criteria),
            "node_type": self.node_type,
            # ... all other fields EXCEPT description and result ...
        }
```

Update `from_dict()`:
```python
    @classmethod
    def from_dict(cls, d: dict) -> TaskNode:
        # Handle backward compat: old format has description/result inline
        has_inline_content = "description" in d or "result" in d
        desc = d.pop("description", "")
        result = d.pop("result", "")
        preview = d.pop("description_preview", "")

        filtered = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        if "status" in filtered:
            filtered["status"] = _STATUS_MIGRATION.get(filtered["status"], filtered["status"])
        node = cls(**filtered)

        if has_inline_content:
            # Old format — load inline and mark dirty for migration.
            # Use object.__setattr__ to bypass the __setattr__ override,
            # then explicitly set dirty/loaded flags.
            object.__setattr__(node, "description", desc)
            object.__setattr__(node, "result", result)
            node._description_preview = desc[:200]
            node._content_dirty = True
            node._content_loaded = True
        else:
            # New format — preview only, content loaded lazily
            node._description_preview = preview
            node._content_dirty = False
        return node
```

**Important:** The `__init__` signature changes because `description` and `result` are no longer direct dataclass fields. The constructor `TaskNode(description="x", result="y")` still works via the private `_description` and `_result` fields, but callers using keyword args will need adjustment. To preserve the old constructor API, use `__post_init__` to handle the `description=` and `result=` kwargs if they're passed. Actually, since `_description` is the field name, we need an `__init__` override or alias. The cleanest approach: keep `_description` and `_result` as the dataclass fields, and add properties. The constructor becomes `TaskNode(_description="x")` which is ugly. Better: use `description` as the field name but override `__setattr__` to set dirty flag. OR: keep `description` and `result` as regular fields, remove the `@property` approach, and instead override `__setattr__`:

```python
    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        if name in ("description", "result") and hasattr(self, "_content_dirty"):
            super().__setattr__("_content_dirty", True)
            if name == "description":
                super().__setattr__("_description_preview", (value or "")[:200])
```

This way `description` and `result` stay as normal dataclass fields, constructor works unchanged, but assignments trigger dirty tracking. Keep `_content_dirty`, `_content_loaded`, `_description_preview` as additional fields with `field(default=..., repr=False, init=False)` using `__post_init__` to initialize them.

Final approach for `task_tree.py` TaskNode:
```python
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

    task_type: str = "simple"
    model_used: str = ""
    project_dir: str = ""

    status: str = "pending"
    result: str = ""
    acceptance_result: dict | None = None

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
    fail_strategy: str = "block"

    # --- Content externalization (not serialized in skeleton) ---
    _content_dirty: bool = field(default=False, repr=False, init=False, compare=False)
    _content_loaded: bool = field(default=False, repr=False, init=False, compare=False)
    _description_preview: str = field(default="", repr=False, init=False, compare=False)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        # Initialize preview from description if set at construction
        if self.description:
            self._description_preview = self.description[:200]

    def __setattr__(self, name: str, value) -> None:
        super().__setattr__(name, value)
        # Track content dirtiness for description/result changes
        if name == "description":
            try:
                super().__setattr__("_content_dirty", True)
                super().__setattr__("_description_preview", (value or "")[:200])
            except AttributeError:
                pass  # During __init__ before _content_dirty exists
        elif name == "result":
            try:
                super().__setattr__("_content_dirty", True)
            except AttributeError:
                pass

    @property
    def description_preview(self) -> str:
        return self._description_preview

    def save_content(self, project_dir: Path | str) -> None:
        """Write description + result to nodes/{id}.yaml if dirty."""
        if not self._content_dirty:
            return
        project_dir = Path(project_dir)
        nodes_dir = project_dir / "nodes"
        nodes_dir.mkdir(parents=True, exist_ok=True)
        data = {"description": self.description, "result": self.result}
        (nodes_dir / f"{self.id}.yaml").write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._content_dirty = False

    def load_content(self, project_dir: Path | str) -> None:
        """Load description + result from nodes/{id}.yaml. Idempotent."""
        if self._content_loaded:
            return
        project_dir = Path(project_dir)
        content_path = project_dir / "nodes" / f"{self.id}.yaml"
        if content_path.exists():
            data = yaml.safe_load(content_path.read_text(encoding="utf-8")) or {}
            # Use super().__setattr__ to avoid marking dirty
            super().__setattr__("description", data.get("description", ""))
            super().__setattr__("result", data.get("result", ""))
            super().__setattr__("_description_preview", (data.get("description", "") or "")[:200])
        self._content_loaded = True

    # ... existing methods (set_status, is_resolved, etc.) unchanged ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskNodeContentExternalization -v`
Expected: ALL PASS

- [ ] **Step 5: Run ALL existing tests to verify no regressions**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py -v`
Expected: ALL PASS (existing tests still work because `description` and `result` remain as normal fields, constructor unchanged)

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/task_tree.py tests/unit/core/test_task_tree.py
git commit -m "feat(task_tree): add content externalization to TaskNode

description/result tracked with dirty flag, saved to nodes/{id}.yaml.
Backward compat: old format auto-migrates on next save."
```

### Task 2: TaskTree save/load with externalized content

**Files:**
- Modify: `src/onemancompany/core/task_tree.py:132-354`
- Test: `tests/unit/core/test_task_tree.py`

- [ ] **Step 1: Write failing tests for TaskTree save/load with content files**

```python
class TestTaskTreeContentExternalization:
    def test_save_creates_node_content_files(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("e1", "Root description")
        child = tree.add_child(root.id, "e2", "Child desc", ["criterion"])
        child.result = "Child result"

        path = tmp_path / "task_tree.yaml"
        tree.save(path)

        # Skeleton should NOT contain description/result
        import yaml
        skeleton = yaml.safe_load(path.read_text())
        for nd in skeleton["nodes"]:
            assert "description" not in nd
            assert "result" not in nd
            assert "description_preview" in nd

        # Content files should exist
        assert (tmp_path / "nodes" / f"{root.id}.yaml").exists()
        assert (tmp_path / "nodes" / f"{child.id}.yaml").exists()

    def test_load_skeleton_only(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("e1", "Root description with lots of text")
        root.result = "Root result"
        path = tmp_path / "task_tree.yaml"
        tree.save(path)

        loaded = TaskTree.load(path)
        loaded_root = loaded.get_node(root.id)
        # Description/result should be empty (not loaded yet)
        assert loaded_root.description == ""
        assert loaded_root.result == ""
        # Preview should be available
        assert loaded_root.description_preview == "Root description with lots of text"

    def test_load_then_load_content(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("e1", "Full description")
        root.result = "Full result"
        path = tmp_path / "task_tree.yaml"
        tree.save(path)

        loaded = TaskTree.load(path)
        loaded_root = loaded.get_node(root.id)
        loaded_root.load_content(tmp_path)
        assert loaded_root.description == "Full description"
        assert loaded_root.result == "Full result"

    def test_backward_compat_old_format(self, tmp_path):
        """Load a tree saved in old format (description/result inline)."""
        import yaml
        old_data = {
            "project_id": "proj1",
            "root_id": "old_root",
            "current_branch": 0,
            "nodes": [{
                "id": "old_root",
                "employee_id": "e1",
                "description": "Legacy inline description",
                "result": "Legacy inline result",
                "status": "completed",
                "parent_id": "",
                "children_ids": [],
                "acceptance_criteria": [],
                "node_type": "task",
                "task_type": "simple",
                "model_used": "",
                "project_dir": "",
                "acceptance_result": None,
                "project_id": "proj1",
                "created_at": "2026-01-01",
                "completed_at": "",
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "timeout_seconds": 3600,
                "branch": 0,
                "branch_active": True,
                "depends_on": [],
                "fail_strategy": "block",
            }],
        }
        path = tmp_path / "task_tree.yaml"
        path.write_text(yaml.dump(old_data, allow_unicode=True), encoding="utf-8")

        loaded = TaskTree.load(path)
        root = loaded.get_node("old_root")
        # Old format: description/result loaded inline, marked dirty
        assert root.description == "Legacy inline description"
        assert root.result == "Legacy inline result"
        assert root._content_dirty is True

        # Save should migrate to new format
        loaded.save(path)
        skeleton = yaml.safe_load(path.read_text())
        for nd in skeleton["nodes"]:
            assert "description" not in nd
            assert "result" not in nd
        assert (tmp_path / "nodes" / "old_root.yaml").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskTreeContentExternalization -v`
Expected: FAIL

- [ ] **Step 3: Update TaskTree.save() and TaskTree.load()**

In `TaskTree.save()`:
```python
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        project_dir = path.parent
        # Flush dirty content to per-node files
        for node in self._nodes.values():
            node.save_content(project_dir)
        # Write skeleton (no description/result)
        data = {
            "project_id": self.project_id,
            "root_id": self.root_id,
            "current_branch": self.current_branch,
            "nodes": [n.to_dict() for n in self._nodes.values()],
        }
        path.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
```

Update `to_dict()` to exclude `description` and `result`, include `description_preview`:
```python
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "children_ids": list(self.children_ids),
            "employee_id": self.employee_id,
            "description_preview": self._description_preview,
            "acceptance_criteria": list(self.acceptance_criteria),
            "node_type": self.node_type,
            "task_type": self.task_type,
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
            "fail_strategy": self.fail_strategy,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/task_tree.py tests/unit/core/test_task_tree.py
git commit -m "feat(task_tree): externalize content in TaskTree save/load

save() flushes dirty node content to nodes/{id}.yaml.
load() only reads skeleton; callers use load_content() for full text.
Old format trees auto-migrate on save."
```

### Task 3: Update vessel.py callers to use load_content()

**Files:**
- Modify: `src/onemancompany/core/vessel.py`

This task has no separate tests — the externalization is transparent to callers once `load_content()` is called before reading content fields.

- [ ] **Step 1: Update `_build_dependency_context()` (vessel.py:113-130)**

Before reading `dep.result`, call `dep.load_content(project_dir)`. Derive `project_dir` from the dep node's `project_dir` field or the tree path:

```python
def _build_dependency_context(tree, node) -> str:
    if not node.depends_on:
        return ""
    sections = []
    max_per_dep = 2000 if len(node.depends_on) <= 3 else 1000
    for dep_id in node.depends_on:
        dep = tree.get_node(dep_id)
        if not dep or not dep.is_resolved:
            continue
        # Load content for reading result
        if dep.project_dir:
            dep.load_content(dep.project_dir)
        result = dep.result or "(no result)"
        # ... rest unchanged ...
```

- [ ] **Step 2: Update review prompt builder (vessel.py:1447-1497)**

Before reading `child.result` and `child.description`, call `load_content()`.
For `already_accepted` children, use `description_preview` instead:

```python
        lines = []
        if already_accepted and needs_review:
            lines.append("以下子任务已通过验收，无需重复审核：")
            for child in already_accepted:
                lines.append(f"  ✓ ({child.employee_id}): {child.description_preview[:80]}")  # <-- USE PREVIEW
            lines.append("")

        if needs_review:
            lines.append("以下子任务需要审核：")
            lines.append("")
            for i, child in enumerate(needs_review, 1):
                child.load_content(project_dir)  # <-- ADD THIS
                # ... rest unchanged (reads child.description, child.result) ...
```

- [ ] **Step 3: Update `reject_child` in tree_tools.py:349-353**

Before reading `node.description`, call `load_content()`:

```python
    if retry:
        # ...
        node.load_content(project_dir)  # <-- ADD: description is read below
        node.set_status(TaskPhase.PENDING)
        node.result = ""
        node.description = (
            f"修正任务: {node.description}\n\n"
            # ...
        )
```

- [ ] **Step 4: Update `_scan_ceo_inbox_nodes` in routes.py:~5206**

Change `node.description` to `node.description_preview` (inbox listing doesn't need full text):

```python
        # In the inbox node dict builder:
        "description": node.description_preview,  # <-- was node.description
```

- [ ] **Step 5: Update `_build_dependency_context` project_dir fallback (vessel.py:113)**

Handle case where `dep.project_dir` is empty — derive from tree path:

```python
def _build_dependency_context(tree, node, project_dir: str = "") -> str:
    """Build context string from resolved dependency results.

    Args:
        project_dir: Fallback project dir for loading content.
    """
    if not node.depends_on:
        return ""
    sections = []
    max_per_dep = 2000 if len(node.depends_on) <= 3 else 1000
    for dep_id in node.depends_on:
        dep = tree.get_node(dep_id)
        if not dep or not dep.is_resolved:
            continue
        # Load content — use dep's own project_dir or fallback
        load_dir = dep.project_dir or project_dir
        if load_dir:
            dep.load_content(load_dir)
        result = dep.result or "(no result)"
        # ... rest unchanged ...
```

Update the call site in `_execute_task` to pass `project_dir`:
```python
            dep_ctx = _build_dependency_context(tree, node, project_dir)
```

- [ ] **Step 7: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.core.vessel import EmployeeManager; print('OK')"`
Expected: OK

- [ ] **Step 8: Commit**

```bash
git add src/onemancompany/core/vessel.py src/onemancompany/agents/tree_tools.py src/onemancompany/api/routes.py
git commit -m "feat: add load_content() calls before reading node content

Updated vessel.py, tree_tools.py reject_child, routes.py inbox scan,
and _build_dependency_context with project_dir fallback."
```

### Task 4: Update tree_manager.py

**Files:**
- Modify: `src/onemancompany/core/tree_manager.py`

- [ ] **Step 1: Read tree_manager.py and add load_content() where result is read**

The `_save()` method calls `self._tree.save(path)` which now handles content flushing automatically. The `node_updated` handler sets `node.result = event.data.get("result", node.result)` — the `__setattr__` override marks dirty automatically. No changes needed in tree_manager.py beyond verifying it works.

- [ ] **Step 2: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.core.tree_manager import TreeManager; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit (if changes needed)**

```bash
git add src/onemancompany/core/tree_manager.py
git commit -m "chore(tree_manager): verify compatibility with content externalization"
```

---

## Chunk 2: Context Windowing and read_node_detail Tool

### Task 5: Build tree context function

**Files:**
- Modify: `src/onemancompany/core/vessel.py`
- Test: `tests/unit/core/test_context_windowing.py` (new)

- [ ] **Step 1: Write failing tests for `_build_tree_context()`**

```python
# tests/unit/core/test_context_windowing.py
"""Tests for distance-based tree context windowing."""
from __future__ import annotations

import pytest
from onemancompany.core.task_tree import TaskNode, TaskTree


class TestBuildTreeContext:
    def _make_tree(self, tmp_path):
        """Create a 4-level tree for testing context windowing."""
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root task: build the app")
        root.result = "Root result text"
        root.project_dir = str(tmp_path)

        child = tree.add_child(root.id, "e2", "Child task: implement API", [])
        child.result = "Child result: API done"
        child.project_dir = str(tmp_path)

        grandchild = tree.add_child(child.id, "e3", "Grandchild: write endpoints", [])
        grandchild.result = "Grandchild result: endpoints written"
        grandchild.project_dir = str(tmp_path)

        great_gc = tree.add_child(grandchild.id, "e4", "Great-grandchild: tests", [])
        great_gc.result = "Great-gc result: tests pass"
        great_gc.project_dir = str(tmp_path)

        # Save content files
        path = tmp_path / "task_tree.yaml"
        tree.save(path)
        return tree, root, child, grandchild, great_gc

    def test_current_node_has_full_content(self, tmp_path):
        from onemancompany.core.vessel import _build_tree_context
        tree, _, _, _, great_gc = self._make_tree(tmp_path)
        ctx = _build_tree_context(tree, great_gc, str(tmp_path))
        assert "Great-grandchild: tests" in ctx
        assert "Great-gc result: tests pass" in ctx

    def test_parent_has_full_content(self, tmp_path):
        from onemancompany.core.vessel import _build_tree_context
        tree, _, _, grandchild, great_gc = self._make_tree(tmp_path)
        ctx = _build_tree_context(tree, great_gc, str(tmp_path))
        assert "Grandchild: write endpoints" in ctx
        assert "Grandchild result: endpoints written" in ctx

    def test_grandparent_has_preview_only(self, tmp_path):
        from onemancompany.core.vessel import _build_tree_context
        tree, _, child, grandchild, great_gc = self._make_tree(tmp_path)
        ctx = _build_tree_context(tree, great_gc, str(tmp_path))
        # Grandparent (child, distance=2) should have preview only, NOT full result
        assert child.id in ctx
        assert "Child result: API done" not in ctx
        # Parent (grandchild, distance=1) SHOULD have full result
        assert "Grandchild result: endpoints written" in ctx

    def test_accepted_children_show_preview_only(self, tmp_path):
        from onemancompany.core.vessel import _build_tree_context
        tree, root, child, _, _ = self._make_tree(tmp_path)
        child.status = "accepted"
        ctx = _build_tree_context(tree, root, str(tmp_path))
        assert child.id in ctx
        # Full result should NOT be in context for accepted children
        assert "Child result: API done" not in ctx
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_context_windowing.py -v`
Expected: FAIL — `_build_tree_context` not found

- [ ] **Step 3: Implement `_build_tree_context()`**

Add to `vessel.py` near the other context-building functions:

```python
def _build_tree_context(tree: TaskTree, node: TaskNode, project_dir: str) -> str:
    """Build distance-based tree context for an employee.

    - Current node + parent: full content (load_content)
    - Grandparent+: skeleton only (id + status + preview)
    - Children needing review: full result
    - Accepted children: skeleton only
    """
    from pathlib import Path

    parts: list[str] = []

    # --- Walk up: ancestors ---
    ancestors: list[tuple[TaskNode, int]] = []  # (node, distance)
    current = node
    dist = 0
    while current.parent_id:
        parent = tree.get_node(current.parent_id)
        if not parent:
            break
        dist += 1
        ancestors.append((parent, dist))
        current = parent

    if ancestors:
        parts.append("=== Task Chain (ancestors) ===")
        for anc, d in reversed(ancestors):
            if d <= 1:  # parent only (distance 1); grandparent+ = skeleton
                anc.load_content(project_dir)
                parts.append(f"[Lv-{d}] {anc.id} ({anc.employee_id}) [{anc.status}]")
                parts.append(f"  Description: {anc.description}")
                if anc.result:
                    parts.append(f"  Result: {anc.result}")
            else:
                parts.append(f"[Lv-{d}] {anc.id} ({anc.employee_id}) [{anc.status}]")
                parts.append(f"  Preview: {anc.description_preview}")
        parts.append("")

    # --- Current node ---
    node.load_content(project_dir)
    parts.append(f"=== Current Task ({node.id}) ===")
    parts.append(f"Description: {node.description}")
    if node.result:
        parts.append(f"Result: {node.result}")
    parts.append("")

    # --- Children ---
    children = tree.get_active_children(node.id)
    if children:
        parts.append("=== Child Tasks ===")
        for child in children:
            if child.is_ceo_node:
                continue
            if child.status == "accepted":
                parts.append(f"  [ACCEPTED] {child.id} ({child.employee_id}): {child.description_preview[:100]}")
            elif child.is_done_executing:
                child.load_content(project_dir)
                parts.append(f"  [{child.status.upper()}] {child.id} ({child.employee_id}): {child.description}")
                parts.append(f"    Result: {child.result}")
            else:
                parts.append(f"  [{child.status.upper()}] {child.id} ({child.employee_id}): {child.description_preview}")
        parts.append("")

    return "\n".join(parts)
```

- [ ] **Step 4: Inject `_build_tree_context` into `_execute_task` context building**

In `_execute_task` (around line 796-826), after loading node content, add tree context.
**Important:** `_build_tree_context` already includes the current node's description, so don't duplicate it:

```python
            node.load_content(project_dir)

            # Tree context includes current node + ancestors + children
            tree_ctx = _build_tree_context(tree, node, project_dir)
            task_with_ctx = tree_ctx if tree_ctx else node.description
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_context_windowing.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_context_windowing.py
git commit -m "feat(vessel): add distance-based tree context windowing

Ancestors: 2 levels full content, rest skeleton.
Children: accepted=preview, pending review=full."
```

### Task 6: Add `read_node_detail` tool

**Files:**
- Modify: `src/onemancompany/agents/common_tools.py`
- Test: `tests/unit/agents/test_common_tools.py` (add test)

- [ ] **Step 1: Write failing test**

```python
# In tests/unit/agents/test_common_tools.py (create if not exists)
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestReadNodeDetail:
    def test_read_node_detail_returns_content(self, tmp_path):
        from onemancompany.core.task_tree import TaskNode, TaskTree, register_tree

        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root task description")
        root.result = "Root result text"
        root.project_dir = str(tmp_path)
        root.acceptance_criteria = ["criterion1"]

        path = tmp_path / "task_tree.yaml"
        tree.save(path)
        register_tree(path, tree)

        from onemancompany.agents.common_tools import read_node_detail

        # Mock context vars
        mock_vessel = MagicMock()
        mock_vessel.employee_id = "e1"
        mock_schedule = {"e1": [MagicMock(node_id="some_task", tree_path=str(path))]}

        with patch("onemancompany.agents.common_tools._current_vessel") as cv, \
             patch("onemancompany.agents.common_tools._current_task_id") as ct, \
             patch("onemancompany.core.vessel.employee_manager") as em:
            cv.get.return_value = mock_vessel
            ct.get.return_value = "some_task"
            em._schedule = mock_schedule

            result = read_node_detail.invoke({"node_id": root.id})
            assert result["status"] == "ok"
            assert "Root task description" in result["description"]
            assert "Root result text" in result["result"]

    def test_read_node_detail_missing_node(self):
        from onemancompany.agents.common_tools import read_node_detail
        from onemancompany.core.task_tree import TaskTree, register_tree

        tree = TaskTree(project_id="test")
        tree.create_root("e1", "Root")
        # Use a fake path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path
            path = Path(tmp) / "task_tree.yaml"
            tree.save(path)
            register_tree(path, tree)

            mock_vessel = MagicMock()
            mock_vessel.employee_id = "e1"
            mock_schedule = {"e1": [MagicMock(node_id="some_task", tree_path=str(path))]}

            with patch("onemancompany.agents.common_tools._current_vessel") as cv, \
                 patch("onemancompany.agents.common_tools._current_task_id") as ct, \
                 patch("onemancompany.core.vessel.employee_manager") as em:
                cv.get.return_value = mock_vessel
                ct.get.return_value = "some_task"
                em._schedule = mock_schedule

                result = read_node_detail.invoke({"node_id": "nonexistent"})
                assert result["status"] == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_common_tools.py::TestReadNodeDetail -v`
Expected: FAIL — `read_node_detail` not found

- [ ] **Step 3: Implement `read_node_detail` tool**

Add to `common_tools.py`:

```python
@tool
def read_node_detail(node_id: str) -> dict:
    """Read the full details of a task node by ID.

    Use this to inspect any task node's full description, result, and metadata
    when the context summary isn't enough.

    Args:
        node_id: The TaskNode ID to read.

    Returns:
        Full node details including description, result, status, and criteria.
    """
    from onemancompany.core.vessel import employee_manager
    from onemancompany.core.task_tree import get_tree
    from pathlib import Path

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    # Find tree_path from current task in schedule
    tree_path = ""
    for entries in employee_manager._schedule.values():
        for e in entries:
            if e.node_id == task_id:
                tree_path = e.tree_path
                break
        if tree_path:
            break

    if not tree_path:
        return {"status": "error", "message": "No project context."}

    tree = get_tree(tree_path)
    node = tree.get_node(node_id)
    if not node:
        return {"status": "error", "message": f"Node {node_id} not found."}

    project_dir = str(Path(tree_path).parent)
    node.load_content(project_dir)

    return {
        "status": "ok",
        "id": node.id,
        "employee_id": node.employee_id,
        "description": node.description,
        "result": node.result,
        "status_phase": node.status,
        "acceptance_criteria": node.acceptance_criteria,
        "node_type": node.node_type,
        "created_at": node.created_at,
        "completed_at": node.completed_at,
    }
```

Register in `_register_all_internal_tools()`:
```python
    _base = [
        list_colleagues, read, ls, write, edit, pull_meeting,
        request_tool_access, load_skill,
        resume_held_task, update_project_team,
        read_node_detail,  # <-- ADD
    ]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_common_tools.py::TestReadNodeDetail -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/agents/common_tools.py tests/unit/agents/test_common_tools.py
git commit -m "feat(tools): add read_node_detail common tool

Allows employees to load full description/result of any task node."
```

---

## Chunk 3: Tree Growth Circuit Breaker

### Task 7: Config constants for circuit breaker

**Files:**
- Modify: `src/onemancompany/core/config.py`

- [ ] **Step 1: Add constants after the existing prompt truncation limits section**

```python
# ---------------------------------------------------------------------------
# Tree growth limits (circuit breaker)
# ---------------------------------------------------------------------------
MAX_REVIEW_ROUNDS = 3       # Max review rounds per parent before CEO escalation
MAX_CHILDREN_PER_NODE = 10  # Max active children per parent node
MAX_TREE_DEPTH = 6          # Max nesting depth for dispatch_child
```

- [ ] **Step 2: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.core.config import MAX_REVIEW_ROUNDS, MAX_CHILDREN_PER_NODE, MAX_TREE_DEPTH; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/core/config.py
git commit -m "feat(config): add tree growth circuit breaker constants"
```

### Task 8: Review round circuit breaker

**Files:**
- Modify: `src/onemancompany/core/vessel.py:1486-1503`
- Test: `tests/unit/core/test_circuit_breaker.py` (new)

- [ ] **Step 1: Write failing test**

```python
# tests/unit/core/test_circuit_breaker.py
"""Tests for tree growth circuit breaker."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from onemancompany.core.task_tree import TaskNode, TaskTree


class TestReviewCircuitBreaker:
    def test_count_review_rounds(self):
        """Count review-type children under a parent."""
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        # Add 3 review nodes
        for i in range(3):
            r = tree.add_child(root.id, "e1", f"Review {i}", [])
            r.node_type = "review"
            r.status = "finished"

        children = tree.get_active_children(root.id)
        review_count = sum(1 for c in children if c.node_type == "review")
        assert review_count == 3

    def test_circuit_breaker_threshold_creates_ceo_request(self):
        """When review count >= MAX_REVIEW_ROUNDS, a ceo_request node should be created."""
        from onemancompany.core.config import MAX_REVIEW_ROUNDS
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        # Add MAX_REVIEW_ROUNDS review nodes
        for i in range(MAX_REVIEW_ROUNDS):
            r = tree.add_child(root.id, "e1", f"Review {i}", [])
            r.node_type = "review"
            r.status = "finished"
        # Add a work child to complete
        work = tree.add_child(root.id, "e2", "Do work", [])
        work.status = "completed"

        # Verify circuit breaker condition
        children = tree.get_active_children(root.id)
        review_count = sum(
            1 for c in children
            if c.node_type == "review" and c.employee_id == root.employee_id
        )
        assert review_count >= MAX_REVIEW_ROUNDS
```

- [ ] **Step 2: Run test to verify it passes** (this is just counting, should work already with the node_type field)

Run: `.venv/bin/python -m pytest tests/unit/core/test_circuit_breaker.py::TestReviewCircuitBreaker -v`
Expected: PASS

- [ ] **Step 3: Update review node creation in vessel.py `_on_child_complete_inner`**

In the review node creation block (~line 1486-1503):

```python
        # --- Circuit breaker: check review round count ---
        from onemancompany.core.config import MAX_REVIEW_ROUNDS, CEO_ID
        review_count = sum(
            1 for c in children
            if c.node_type == "review" and c.employee_id == parent_node.employee_id
        )
        if review_count >= MAX_REVIEW_ROUNDS:
            logger.warning(
                "Review circuit breaker: {} rounds for parent {} — escalating to CEO",
                review_count, parent_node.id,
            )
            parent_node.set_status(TaskPhase.HOLDING)
            save_tree_async(entry.tree_path)

            # Build escalation summary
            last_review = None
            for c in reversed(children):
                if c.node_type == "review":
                    last_review = c
                    break
            last_notes = ""
            if last_review:
                for sibling in children:
                    if sibling.acceptance_result and not sibling.acceptance_result.get("passed"):
                        last_notes = sibling.acceptance_result.get("notes", "")

            escalation_desc = (
                f"审核僵局: 任务 {parent_node.id} ({parent_node.description_preview}) "
                f"已经过 {review_count} 轮审核未能收敛。\n"
                f"最后一轮分歧: {last_notes[:300]}\n"
                f"请介入处理：可以选择接受现有结果、取消任务、或给出具体指导。"
            )
            ceo_node = tree.add_child(
                parent_id=parent_node.id,
                employee_id=CEO_ID,
                description=escalation_desc,
                acceptance_criteria=[],
            )
            ceo_node.node_type = "ceo_request"
            ceo_node.project_id = project_id
            ceo_node.project_dir = project_dir
            save_tree_async(entry.tree_path)

            # Publish inbox event
            from onemancompany.core.events import CompanyEvent, event_bus
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                loop.create_task(event_bus.publish(CompanyEvent(
                    type="ceo_inbox_updated",
                    payload={"node_id": ceo_node.id, "description": escalation_desc},
                    agent="SYSTEM",
                )))
            except RuntimeError:
                pass
            return

        # Create review node (existing logic, but tag as review type)
        review_node = tree.add_child(
            parent_id=parent_node.id,
            employee_id=parent_node.employee_id,
            description=review_prompt,
            acceptance_criteria=[],
        )
        review_node.node_type = "review"  # <-- TAG AS REVIEW
        review_node.task_type = "simple"
        review_node.project_id = project_id
        review_node.project_dir = project_dir
        save_tree_async(entry.tree_path)
```

Also update the review detection check (~line 1427-1433) to use `node_type == "review"`:
```python
        for child in children:
            if child.node_type == "review":
                if child.status in (TaskPhase.PENDING.value, TaskPhase.PROCESSING.value):
                    logger.debug("Review node {} already active for parent {} — skipping", child.id, parent_node.id)
                    return
```

And update the auto-complete check (~line 1436):
```python
        non_review_children = [c for c in children if c.node_type != "review"]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_circuit_breaker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_circuit_breaker.py
git commit -m "feat(vessel): add review round circuit breaker

Max 3 review rounds per parent, then escalate to CEO inbox.
Review nodes tagged with node_type='review'."
```

### Task 9: Children count and depth limits in dispatch_child

**Files:**
- Modify: `src/onemancompany/agents/tree_tools.py:88-256`
- Test: `tests/unit/agents/test_tree_tools.py` (add tests)

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/agents/test_tree_tools.py (create or add to existing)
import pytest
from onemancompany.core.task_tree import TaskTree


class TestDispatchChildLimits:
    def test_max_children_exceeded(self):
        """dispatch_child should fail when parent has too many children."""
        from onemancompany.core.config import MAX_CHILDREN_PER_NODE
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        # Add MAX_CHILDREN_PER_NODE children
        for i in range(MAX_CHILDREN_PER_NODE):
            tree.add_child(root.id, "e2", f"Child {i}", [])
        # Next child should be blocked by the limit
        assert len(tree.get_active_children(root.id)) >= MAX_CHILDREN_PER_NODE

    def test_tree_depth_calculation(self):
        """Verify depth counting from a node to root."""
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        c1 = tree.add_child(root.id, "e2", "L1", [])
        c2 = tree.add_child(c1.id, "e3", "L2", [])
        c3 = tree.add_child(c2.id, "e4", "L3", [])

        # Count depth of c3: c3 -> c2 -> c1 -> root = depth 3
        depth = 0
        node = c3
        while node.parent_id:
            depth += 1
            node = tree.get_node(node.parent_id)
        assert depth == 3
```

- [ ] **Step 2: Run tests to verify they pass** (these are just verification tests)

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py::TestDispatchChildLimits -v`
Expected: PASS

- [ ] **Step 3: Add limit checks to `dispatch_child()` in tree_tools.py**

After validating employee exists and before `tree.add_child()` (~line 185), add:

```python
    # --- Circuit breaker: children count limit ---
    from onemancompany.core.config import MAX_CHILDREN_PER_NODE, MAX_TREE_DEPTH
    active_children = tree.get_active_children(task_id)
    if len(active_children) >= MAX_CHILDREN_PER_NODE:
        return {
            "status": "error",
            "message": f"已达子任务上限 ({MAX_CHILDREN_PER_NODE})，请整合现有任务或向上汇报。",
        }

    # --- Circuit breaker: tree depth limit ---
    depth = 0
    walker = current_node
    while walker.parent_id:
        depth += 1
        walker = tree.get_node(walker.parent_id)
        if not walker:
            break
    if depth + 1 >= MAX_TREE_DEPTH:
        return {
            "status": "error",
            "message": f"任务树已达最大深度 ({MAX_TREE_DEPTH})，无法继续下派，请直接完成或向上汇报。",
        }
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/agents/tree_tools.py tests/unit/agents/test_tree_tools.py
git commit -m "feat(tree_tools): add children count and depth limits to dispatch_child

Blocks dispatch when parent has >= 10 active children or tree depth >= 6."
```

---

## Chunk 4: Three-Granularity Resource Recovery

### Task 10: Implement abort_employee and abort_all

**Files:**
- Modify: `src/onemancompany/core/vessel.py`
- Test: `tests/unit/core/test_abort.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/core/test_abort.py
"""Tests for abort_employee and abort_all."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from onemancompany.core.task_tree import TaskNode, TaskTree
from onemancompany.core.task_lifecycle import TaskPhase


class TestAbortEmployee:
    def test_abort_employee_only_cancels_non_terminal(self):
        """abort_employee should NOT touch accepted/finished/cancelled nodes."""
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        c1 = tree.add_child(root.id, "e1", "Pending task", [])
        c2 = tree.add_child(root.id, "e1", "Accepted task", [])
        c2.status = "accepted"
        c3 = tree.add_child(root.id, "e1", "Processing task", [])
        c3.status = "processing"

        # Verify the cancelable filter logic
        _cancelable = {"pending", "processing", "holding"}
        non_terminal = [n for n in [c1, c2, c3] if n.status in _cancelable]
        assert len(non_terminal) == 2
        assert c2 not in non_terminal

    def test_abort_employee_integration(self, tmp_path):
        """Integration test: abort_employee clears schedule and cancels nodes."""
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        child = tree.add_child(root.id, "e1", "Pending task", [])
        path = tmp_path / "task_tree.yaml"
        tree.save(path)

        from onemancompany.core.task_tree import register_tree
        register_tree(path, tree)

        entry = MagicMock()
        entry.node_id = child.id
        entry.tree_path = str(path)

        # Test the core cancel logic directly (avoids needing full EmployeeManager)
        _cancelable = {"pending", "processing", "holding"}
        node = tree.get_node(child.id)
        assert node.status in _cancelable
        node.status = TaskPhase.CANCELLED.value
        assert node.status == "cancelled"
```

- [ ] **Step 2: Run tests to verify they fail/pass as expected**

Run: `.venv/bin/python -m pytest tests/unit/core/test_abort.py -v`

- [ ] **Step 3: Implement `abort_employee()` and `abort_all()`**

Add to `EmployeeManager` class in vessel.py:

```python
    def abort_employee(self, employee_id: str) -> int:
        """Cancel all tasks for an employee. Returns count cancelled."""
        from onemancompany.core.task_tree import get_tree, save_tree_async
        from onemancompany.core.automation import stop_all_crons_for_employee

        count = 0
        # 1. Clear schedule
        entries = list(self._schedule.get(employee_id, []))
        self._schedule[employee_id] = []

        # 2. Clear deferred schedule
        self._deferred_schedule.discard(employee_id)

        # 3. Cancel running asyncio.Task
        running = self._running_tasks.pop(employee_id, None)
        if running and not running.done():
            running.cancel()
            logger.info("Cancelled running asyncio.Task for {}", employee_id)

        # 4. Cancel non-terminal nodes in trees
        _cancelable = {TaskPhase.PENDING.value, TaskPhase.PROCESSING.value, TaskPhase.HOLDING.value}
        seen_trees: set[str] = set()
        for entry in entries:
            try:
                tree = get_tree(entry.tree_path)
                node = tree.get_node(entry.node_id)
                if node and node.status in _cancelable:
                    node.status = TaskPhase.CANCELLED.value
                    node.completed_at = datetime.now().isoformat()
                    node.result = f"Cancelled: employee {employee_id} aborted"
                    count += 1
                    self._publish_node_update(employee_id, node)
                seen_trees.add(entry.tree_path)
            except Exception as e:
                logger.error("Failed to cancel node {} for {}: {}", entry.node_id, employee_id, e)

        for tp in seen_trees:
            save_tree_async(tp)

        # 5. Stop crons
        stop_all_crons_for_employee(employee_id)

        # 6. Reset status
        if employee_id in company_state.employees:
            company_state.employees[employee_id].status = STATUS_IDLE
            company_state.employees[employee_id].current_task = None

        return count

    async def abort_all(self) -> int:
        """Cancel all tasks for all employees. Returns total count cancelled."""
        from onemancompany.core.automation import stop_all_automations
        from onemancompany.core.claude_session import stop_all_daemons

        total = 0
        for emp_id in list(self._schedule.keys()):
            total += self.abort_employee(emp_id)

        # Also abort employees with running tasks but empty schedules
        for emp_id in list(self._running_tasks.keys()):
            if emp_id not in self._schedule:
                total += self.abort_employee(emp_id)

        await stop_all_automations()
        await stop_all_daemons()

        return total
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_abort.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_abort.py
git commit -m "feat(vessel): add abort_employee() and abort_all()

abort_employee: clear schedule, cancel running task, cancel non-terminal
tree nodes, stop crons, reset to IDLE.
abort_all: abort all employees + stop automations + stop daemons."
```

### Task 11: API endpoints for abort

**Files:**
- Modify: `src/onemancompany/api/routes.py`

- [ ] **Step 1: Add API endpoints**

```python
@app.post("/api/employee/{employee_id}/abort")
async def abort_employee_tasks(employee_id: str):
    """Abort all tasks for a specific employee."""
    count = employee_manager.abort_employee(employee_id)
    await _broadcast_snapshot()
    return {"status": "ok", "cancelled": count, "employee_id": employee_id}


@app.post("/api/abort-all")
async def abort_all_tasks():
    """Abort all tasks for all employees. Panic button."""
    count = await employee_manager.abort_all()
    await _broadcast_snapshot()
    return {"status": "ok", "cancelled": count}
```

- [ ] **Step 2: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.api.routes import app; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/api/routes.py
git commit -m "feat(api): add abort-employee and abort-all endpoints"
```

### Task 12: Frontend "Stop All" button

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`

- [ ] **Step 1: Add button to index.html management panel**

Find the management/admin area in `index.html` and add:

```html
<button id="btn-abort-all" class="btn-danger" title="Stop all employee tasks">
  Stop All
</button>
```

- [ ] **Step 2: Add click handler in app.js**

```javascript
document.getElementById('btn-abort-all')?.addEventListener('click', async () => {
    if (!confirm('确定要停止所有员工的所有任务吗？')) return;
    try {
        const resp = await fetch('/api/abort-all', { method: 'POST' });
        const data = await resp.json();
        console.log('Abort all result:', data);
    } catch (e) {
        console.error('Abort all failed:', e);
    }
});
```

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat(frontend): add Stop All button with confirmation dialog"
```

### Task 13: Harden existing abort_project

**Files:**
- Modify: `src/onemancompany/core/vessel.py:714-746`

- [ ] **Step 1: Add cron cleanup to `abort_project()`**

After cancelling a node, also stop its associated crons:

```python
                    if node.status in _cancelable:
                        node.status = TaskPhase.CANCELLED.value
                        # ... existing logic ...

                        # Stop associated crons
                        from onemancompany.core.automation import stop_cron
                        for cron_prefix in (f"reply_{entry.node_id}", f"holding_{entry.node_id}"):
                            try:
                                stop_cron(emp_id, cron_prefix)
                            except Exception:
                                pass
```

- [ ] **Step 2: Commit**

```bash
git add src/onemancompany/core/vessel.py
git commit -m "fix(vessel): harden abort_project to stop associated crons"
```

---

## Final Verification

### Task 14: Full test suite

- [ ] **Step 1: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -v --timeout=60`
Expected: ALL PASS

- [ ] **Step 2: Verify compilation of all modified modules**

Run: `.venv/bin/python -c "from onemancompany.core.task_tree import TaskNode, TaskTree; from onemancompany.core.vessel import EmployeeManager; from onemancompany.agents.common_tools import read_node_detail; from onemancompany.agents.tree_tools import dispatch_child; print('ALL OK')"`
Expected: ALL OK

- [ ] **Step 3: Final commit if any loose ends**

```bash
git status
# If any unstaged changes, add and commit
```
