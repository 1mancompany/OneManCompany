# CEO Unified Conversation Model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all CEO interaction paths with a per-project multi-session conversation model using CeoExecutor (Launcher implementation), where system messages and CEO replies flow through a unified TUI.

**Architecture:** CeoExecutor implements the Launcher ABC, pushing messages into per-project CeoSession queues managed by a CeoBroker singleton. CEO replies resolve asyncio Futures, returning LaunchResult to the normal task execution pipeline. Frontend shows project list (left) + conversation (right), like Claude Code multi-session.

**Tech Stack:** Python asyncio, FastAPI, vanilla JS + WebSocket, YAML persistence

**Spec:** `docs/superpowers/specs/2026-03-29-ceo-executor-design.md`
**Audit:** `docs/superpowers/specs/2026-03-29-ceo-executor-audit.md`

---

### Task 1: Data Structures — CeoInteraction + CeoSession + persistence

**Files:**
- Create: `src/onemancompany/core/ceo_broker.py`
- Test: `tests/unit/core/test_ceo_broker.py`

This task creates the data structures only — no routing logic yet.

- [ ] **Step 1: Write failing tests for CeoInteraction and CeoSession**

```python
# tests/unit/core/test_ceo_broker.py
"""Tests for CeoBroker data structures and persistence."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
import yaml

from onemancompany.core.ceo_broker import CeoInteraction, CeoSession


class TestCeoInteraction:
    def test_creation(self):
        future = asyncio.get_event_loop().create_future()
        interaction = CeoInteraction(
            node_id="abc123",
            tree_path="/tmp/tree.yaml",
            project_id="proj_001/iter_001",
            source_employee="00003",
            interaction_type="ceo_request",
            message="Alex requests deployment approval",
            future=future,
        )
        assert interaction.node_id == "abc123"
        assert interaction.interaction_type == "ceo_request"
        assert interaction.created_at  # auto-filled


class TestCeoSession:
    def test_push_system_message(self):
        session = CeoSession(project_id="proj_001")
        session.push_system_message("Deploy approval needed", source="00003")
        assert len(session.history) == 1
        assert session.history[0]["role"] == "system"
        assert session.history[0]["source"] == "00003"

    def test_push_ceo_message(self):
        session = CeoSession(project_id="proj_001")
        session.push_ceo_message("Approved")
        assert len(session.history) == 1
        assert session.history[0]["role"] == "ceo"

    def test_enqueue_and_has_pending(self):
        session = CeoSession(project_id="proj_001")
        assert session.has_pending is False
        future = asyncio.get_event_loop().create_future()
        interaction = CeoInteraction(
            node_id="abc",
            tree_path="/tmp/t.yaml",
            project_id="proj_001",
            source_employee="00003",
            interaction_type="ceo_request",
            message="Need approval",
            future=future,
        )
        session.enqueue(interaction)
        assert session.has_pending is True
        assert session.pending_count == 1

    def test_save_and_load_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session = CeoSession(project_id="proj_001")
            session.push_system_message("Hello", source="00003")
            session.push_ceo_message("Hi")
            session.save_history(Path(tmpdir))

            session2 = CeoSession(project_id="proj_001")
            session2.load_history(Path(tmpdir))
            assert len(session2.history) == 2
            assert session2.history[0]["role"] == "system"
            assert session2.history[1]["role"] == "ceo"

    def test_fifo_order(self):
        session = CeoSession(project_id="proj_001")
        loop = asyncio.get_event_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()
        i1 = CeoInteraction(
            node_id="first", tree_path="", project_id="proj_001",
            source_employee="00003", interaction_type="ceo_request",
            message="First", future=f1,
        )
        i2 = CeoInteraction(
            node_id="second", tree_path="", project_id="proj_001",
            source_employee="00004", interaction_type="project_confirm",
            message="Second", future=f2,
        )
        session.enqueue(i1)
        session.enqueue(i2)
        popped = session.pop_pending()
        assert popped.node_id == "first"
        assert session.pending_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_ceo_broker.py -v`
Expected: ImportError — `ceo_broker` module doesn't exist

- [ ] **Step 3: Implement CeoInteraction and CeoSession**

```python
# src/onemancompany/core/ceo_broker.py
"""CeoBroker — unified CEO conversation model.

Each project has a CeoSession with independent conversation history
and a FIFO queue of pending interactions (requests awaiting CEO reply).
CeoExecutor pushes interactions; CEO replies resolve them.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger

from onemancompany.core.config import ENCODING_UTF8

# Persistence filename for CEO session history within project dir
CEO_SESSION_FILENAME = "ceo_session.yaml"


@dataclass
class CeoInteraction:
    """A single pending interaction awaiting CEO reply."""
    node_id: str
    tree_path: str
    project_id: str
    source_employee: str
    interaction_type: str       # "ceo_request" | "project_confirm"
    message: str
    future: asyncio.Future
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class CeoSession:
    """Per-project CEO conversation session."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.history: list[dict] = []
        self._pending: deque[CeoInteraction] = deque()

    @property
    def has_pending(self) -> bool:
        return len(self._pending) > 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def enqueue(self, interaction: CeoInteraction) -> None:
        """Add a pending interaction to the FIFO queue."""
        self._pending.append(interaction)
        # Also add as system message in history
        self.push_system_message(interaction.message, source=interaction.source_employee)

    def pop_pending(self) -> CeoInteraction | None:
        """Pop the front of the pending queue (FIFO)."""
        if self._pending:
            return self._pending.popleft()
        return None

    def push_system_message(self, text: str, source: str = "") -> dict:
        """Append a system message to conversation history."""
        msg = {
            "role": "system",
            "text": text,
            "source": source,
            "timestamp": datetime.now().isoformat(),
        }
        self.history.append(msg)
        return msg

    def push_ceo_message(self, text: str) -> dict:
        """Append a CEO message to conversation history."""
        msg = {
            "role": "ceo",
            "text": text,
            "timestamp": datetime.now().isoformat(),
        }
        self.history.append(msg)
        return msg

    def save_history(self, project_dir: Path) -> None:
        """Persist conversation history to disk."""
        path = project_dir / CEO_SESSION_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump({"history": self.history}, allow_unicode=True, sort_keys=False),
            encoding=ENCODING_UTF8,
        )

    def load_history(self, project_dir: Path) -> None:
        """Load conversation history from disk."""
        path = project_dir / CEO_SESSION_FILENAME
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding=ENCODING_UTF8)) or {}
            self.history = data.get("history", [])

    def to_summary(self) -> dict:
        """Return summary for project list UI."""
        return {
            "project_id": self.project_id,
            "has_pending": self.has_pending,
            "pending_count": self.pending_count,
            "message_count": len(self.history),
            "last_message": self.history[-1] if self.history else None,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_ceo_broker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/ceo_broker.py tests/unit/core/test_ceo_broker.py
git commit -m "feat(ceo-executor): CeoInteraction + CeoSession data structures"
```

---

### Task 2: CeoBroker — session management + routing logic

**Files:**
- Modify: `src/onemancompany/core/ceo_broker.py`
- Test: `tests/unit/core/test_ceo_broker.py`

- [ ] **Step 1: Write failing tests for CeoBroker**

```python
# Append to tests/unit/core/test_ceo_broker.py

class TestCeoBroker:
    def test_get_or_create_session(self):
        from onemancompany.core.ceo_broker import CeoBroker
        broker = CeoBroker()
        session = broker.get_or_create_session("proj_001")
        assert session.project_id == "proj_001"
        # Second call returns same instance
        session2 = broker.get_or_create_session("proj_001")
        assert session is session2

    def test_list_sessions_sorted_by_pending(self):
        from onemancompany.core.ceo_broker import CeoBroker
        broker = CeoBroker()
        s1 = broker.get_or_create_session("proj_no_pending")
        s2 = broker.get_or_create_session("proj_with_pending")
        loop = asyncio.get_event_loop()
        s2.enqueue(CeoInteraction(
            node_id="x", tree_path="", project_id="proj_with_pending",
            source_employee="00003", interaction_type="ceo_request",
            message="Help", future=loop.create_future(),
        ))
        summaries = broker.list_sessions()
        # Pending-first
        assert summaries[0]["project_id"] == "proj_with_pending"
        assert summaries[1]["project_id"] == "proj_no_pending"

    @pytest.mark.asyncio
    async def test_handle_input_resolves_pending(self):
        from onemancompany.core.ceo_broker import CeoBroker
        broker = CeoBroker()
        session = broker.get_or_create_session("proj_001")
        future = asyncio.get_event_loop().create_future()
        session.enqueue(CeoInteraction(
            node_id="abc", tree_path="", project_id="proj_001",
            source_employee="00003", interaction_type="ceo_request",
            message="Need approval", future=future,
        ))
        result = await broker.handle_input("proj_001", "Approved")
        assert result["type"] == "resolved"
        assert result["node_id"] == "abc"
        assert future.result() == "Approved"
        assert session.has_pending is False

    @pytest.mark.asyncio
    async def test_handle_input_no_pending_returns_followup(self):
        from onemancompany.core.ceo_broker import CeoBroker
        broker = CeoBroker()
        broker.get_or_create_session("proj_001")
        result = await broker.handle_input("proj_001", "Do more work")
        assert result["type"] == "followup"
        assert result["text"] == "Do more work"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_ceo_broker.py::TestCeoBroker -v`
Expected: ImportError — CeoBroker not defined

- [ ] **Step 3: Implement CeoBroker**

Add to `src/onemancompany/core/ceo_broker.py`:

```python
class CeoBroker:
    """Central manager for all CEO per-project sessions.

    Singleton — access via get_ceo_broker().
    """

    def __init__(self) -> None:
        self._sessions: dict[str, CeoSession] = {}

    def get_or_create_session(self, project_id: str) -> CeoSession:
        """Get existing session or create a new one."""
        if project_id not in self._sessions:
            self._sessions[project_id] = CeoSession(project_id=project_id)
        return self._sessions[project_id]

    def get_session(self, project_id: str) -> CeoSession | None:
        """Get session if it exists, else None."""
        return self._sessions.get(project_id)

    def list_sessions(self) -> list[dict]:
        """List all sessions, sorted by pending-first then recency."""
        summaries = [s.to_summary() for s in self._sessions.values()]
        summaries.sort(key=lambda s: (not s["has_pending"], s["project_id"]))
        return summaries

    async def handle_input(self, project_id: str, text: str) -> dict:
        """Route CEO input to the correct handler.

        Returns:
            {"type": "resolved", "node_id": str} — resolved a pending interaction
            {"type": "followup", "text": str} — no pending, treat as follow-up
        """
        session = self.get_or_create_session(project_id)
        if session.has_pending:
            interaction = session.pop_pending()
            session.push_ceo_message(text)
            interaction.future.set_result(text)
            logger.info(
                "[CeoBroker] Resolved pending {} for project={} node={}",
                interaction.interaction_type, project_id, interaction.node_id,
            )
            return {"type": "resolved", "node_id": interaction.node_id}
        else:
            session.push_ceo_message(text)
            logger.info("[CeoBroker] No pending for project={} — followup", project_id)
            return {"type": "followup", "text": text}


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_broker: CeoBroker | None = None


def get_ceo_broker() -> CeoBroker:
    """Get the global CeoBroker singleton."""
    global _broker
    if _broker is None:
        _broker = CeoBroker()
    return _broker
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_ceo_broker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/ceo_broker.py tests/unit/core/test_ceo_broker.py
git commit -m "feat(ceo-executor): CeoBroker session management + routing"
```

---

### Task 3: CeoExecutor — Launcher implementation

**Files:**
- Modify: `src/onemancompany/core/ceo_broker.py` (add CeoExecutor class)
- Test: `tests/unit/core/test_ceo_broker.py`

- [ ] **Step 1: Write failing test for CeoExecutor**

```python
# Append to tests/unit/core/test_ceo_broker.py
from unittest.mock import MagicMock, patch

class TestCeoExecutor:
    @pytest.mark.asyncio
    async def test_execute_enqueues_and_waits(self):
        """CeoExecutor.execute() should enqueue interaction and await CEO reply."""
        from onemancompany.core.ceo_broker import CeoExecutor, get_ceo_broker
        from onemancompany.core.vessel import TaskContext, LaunchResult

        # Reset broker
        import onemancompany.core.ceo_broker as _mod
        _mod._broker = None
        broker = get_ceo_broker()

        executor = CeoExecutor()

        context = TaskContext(
            project_id="proj_001/iter_001",
            work_dir="/tmp",
            employee_id="00001",
            task_id="node_abc",
        )

        # Simulate CEO replying after a short delay
        async def _reply_later():
            await asyncio.sleep(0.05)
            session = broker.get_session("proj_001/iter_001")
            interaction = session.pop_pending()
            interaction.future.set_result("CEO says approved")

        reply_task = asyncio.create_task(_reply_later())

        with patch("onemancompany.core.ceo_broker.event_bus") as mock_bus:
            mock_bus.publish = MagicMock(return_value=asyncio.coroutine(lambda: None)())
            result = await executor.execute("Deploy approval needed", context)

        await reply_task
        assert isinstance(result, LaunchResult)
        assert result.output == "CEO says approved"
        assert result.model_used == "ceo"

        _mod._broker = None  # cleanup

    def test_is_ready(self):
        from onemancompany.core.ceo_broker import CeoExecutor
        executor = CeoExecutor()
        assert executor.is_ready() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_ceo_broker.py::TestCeoExecutor -v`
Expected: ImportError — CeoExecutor not defined

- [ ] **Step 3: Implement CeoExecutor**

Add to `src/onemancompany/core/ceo_broker.py`:

```python
from onemancompany.core.vessel import Launcher, LaunchResult, TaskContext
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.models import EventType
from onemancompany.core.config import SYSTEM_AGENT


class CeoExecutor(Launcher):
    """Virtual executor for CEO (00001).

    Does not call any LLM. Pushes the task as a message into the project's
    CeoSession, then waits for the CEO to reply in the TUI. The reply
    becomes the LaunchResult output.
    """

    async def execute(
        self,
        task_description: str,
        context: TaskContext,
        on_log: Callable[[str, str], None] | None = None,
    ) -> LaunchResult:
        broker = get_ceo_broker()
        project_id = context.project_id or "default"
        session = broker.get_or_create_session(project_id)

        future = asyncio.get_event_loop().create_future()
        interaction = CeoInteraction(
            node_id=context.task_id,
            tree_path="",  # filled by caller if needed
            project_id=project_id,
            source_employee=context.employee_id,
            interaction_type="ceo_request",
            message=task_description,
            future=future,
        )
        session.enqueue(interaction)

        # Broadcast to frontend: new message in this project's session
        await event_bus.publish(CompanyEvent(
            type=EventType.CEO_SESSION_MESSAGE,
            payload={
                "project_id": project_id,
                "node_id": context.task_id,
                "message": task_description,
                "source_employee": context.employee_id,
                "interaction_type": "ceo_request",
            },
            agent=SYSTEM_AGENT,
        ))

        if on_log:
            on_log("ceo_request", f"Awaiting CEO reply for: {task_description[:100]}")

        logger.info("[CeoExecutor] Enqueued request for project={} node={}", project_id, context.task_id)

        # Wait for CEO to reply
        ceo_response = await future

        # Persist updated history
        if context.work_dir:
            session.save_history(Path(context.work_dir))

        return LaunchResult(output=ceo_response, model_used="ceo")
```

Also add `from typing import Callable` to imports, and add `CEO_SESSION_MESSAGE` to EventType later (Task 6).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_ceo_broker.py::TestCeoExecutor -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -x -q`
Expected: All pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/ceo_broker.py tests/unit/core/test_ceo_broker.py
git commit -m "feat(ceo-executor): CeoExecutor Launcher implementation"
```

---

### Task 4: Register CeoExecutor at startup + add CEO_SESSION_MESSAGE event

**Files:**
- Modify: `src/onemancompany/core/models.py` (add EventType)
- Modify: `src/onemancompany/main.py` or startup code (register CEO executor)
- Test: `tests/unit/core/test_ceo_broker.py`

- [ ] **Step 1: Add CEO_SESSION_MESSAGE to EventType**

In `src/onemancompany/core/models.py`, add after `CEO_REPORT`:
```python
CEO_SESSION_MESSAGE = "ceo_session_message"
```

- [ ] **Step 2: Write failing test for CEO registration at startup**

```python
# Append to tests/unit/core/test_ceo_broker.py

class TestCeoRegistration:
    def test_register_ceo_executor(self):
        """CeoExecutor should be registerable via employee_manager.register()."""
        from onemancompany.core.ceo_broker import CeoExecutor
        from onemancompany.core.vessel import EmployeeManager
        from onemancompany.core.config import CEO_ID

        em = EmployeeManager.__new__(EmployeeManager)
        em.executors = {}
        em.configs = {}
        em.task_histories = {}
        em._history_summaries = {}
        em.vessels = {}
        em._schedule = {}
        em._running_tasks = {}

        executor = CeoExecutor()
        em.executors[CEO_ID] = executor

        assert CEO_ID in em.executors
        assert isinstance(em.executors[CEO_ID], CeoExecutor)
        assert executor.is_ready()
```

- [ ] **Step 3: Run test to verify it passes** (this is a basic wiring test)

Run: `.venv/bin/python -m pytest tests/unit/core/test_ceo_broker.py::TestCeoRegistration -v`

- [ ] **Step 4: Add CEO registration to startup**

Find the startup code where `register_founding_employee` is called (in `main.py` lifespan). Add after the founding employee loop:

```python
# Register CeoExecutor for CEO (virtual employee — no LLM, routes to TUI)
from onemancompany.core.ceo_broker import CeoExecutor
from onemancompany.core.config import CEO_ID
ceo_executor = CeoExecutor()
employee_manager.executors[CEO_ID] = ceo_executor
logger.info("[startup] Registered CEO ({}) — CeoExecutor (TUI routing)", CEO_ID)
```

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -x -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/models.py src/onemancompany/main.py tests/unit/core/test_ceo_broker.py
git commit -m "feat(ceo-executor): register CeoExecutor at startup + CEO_SESSION_MESSAGE event"
```

---

### Task 5: Modify dispatch_child — remove CEO special path

**Files:**
- Modify: `src/onemancompany/agents/tree_tools.py`
- Test: `tests/unit/agents/test_tree_tools_ceo.py` (update existing tests)

After this task, `dispatch_child(CEO_ID, ...)` uses normal `schedule_node` → CeoExecutor. The CEO_REQUEST node type is still set, but routing goes through the executor system.

- [ ] **Step 1: Write test for new behavior**

```python
# In tests/unit/agents/test_tree_tools_ceo.py (or new file)

def test_dispatch_child_ceo_uses_schedule_node():
    """dispatch_child(CEO_ID) should call schedule_node, not the old special path."""
    # Setup: mock tree, mock employee_manager
    # Call dispatch_child with CEO_ID
    # Assert: schedule_node was called with CEO_ID
    # Assert: node_type is still CEO_REQUEST
    # Assert: parent hold_reason is set
    # Assert: no direct CEO_INBOX_UPDATED event (CeoExecutor handles that)
```

(Exact test code depends on existing test patterns in `test_tree_tools_ceo.py` — read that file first.)

- [ ] **Step 2: Modify dispatch_child**

In `src/onemancompany/agents/tree_tools.py`, replace the CEO special path (lines ~364-388) with:

```python
if employee_id == CEO_ID:
    child.node_type = NodeType.CEO_REQUEST
    current_node.hold_reason = f"ceo_request={child.id},no_watchdog=1"
    _save_tree(project_dir, tree)
    # Use normal schedule_node path — CeoExecutor handles the rest
    from onemancompany.core.vessel import employee_manager
    employee_manager.schedule_node(employee_id, child.id, tree_path_str)
    employee_manager._schedule_next(employee_id)
    return {
        "status": "dispatched",
        "node_id": child.id,
        "employee_id": employee_id,
        "description": description,
        "node_type": NodeType.CEO_REQUEST,
        "ceo_request": True,
        "message": (
            "Task dispatched to CEO inbox. Your task will automatically pause (HOLDING) "
            "until the CEO responds. You should finish your current output now — "
            "the system handles the rest."
        ),
    }
```

Remove: `schedule_auto_open_inbox` call, direct `CEO_INBOX_UPDATED` event publish.

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools_ceo.py -v`
Run: `.venv/bin/python -m pytest tests/unit/ -x -q`

- [ ] **Step 4: Commit**

```bash
git add src/onemancompany/agents/tree_tools.py tests/unit/agents/test_tree_tools_ceo.py
git commit -m "feat(ceo-executor): dispatch_child(CEO_ID) uses schedule_node path"
```

---

### Task 6: Replace project completion flow

**Files:**
- Modify: `src/onemancompany/core/vessel.py`
- Test: `tests/unit/core/test_ceo_broker.py`

Replace `_request_ceo_confirmation` + auto-confirm timer with CeoExecutor path. When project completes, schedule a "project_confirm" node on CEO → CeoExecutor enqueues in session → CEO replies → cleanup runs.

- [ ] **Step 1: Write failing test**

```python
# Append to tests/unit/core/test_ceo_broker.py

class TestProjectConfirmViaExecutor:
    @pytest.mark.asyncio
    async def test_project_completion_enqueues_confirm(self):
        """When project completes, a confirm interaction should appear in CeoSession."""
        import onemancompany.core.ceo_broker as _mod
        _mod._broker = None
        from onemancompany.core.ceo_broker import get_ceo_broker

        broker = get_ceo_broker()
        session = broker.get_or_create_session("proj_001/iter_001")

        # Before: no pending
        assert session.has_pending is False

        # Simulate CeoExecutor being called for project confirm
        from onemancompany.core.ceo_broker import CeoExecutor, CeoInteraction
        future = asyncio.get_event_loop().create_future()
        session.enqueue(CeoInteraction(
            node_id="ceo_root",
            tree_path="",
            project_id="proj_001/iter_001",
            source_employee="00004",
            interaction_type="project_confirm",
            message="Project complete. Confirm?",
            future=future,
        ))

        assert session.has_pending is True
        assert session.pending_count == 1

        # CEO confirms
        interaction = session.pop_pending()
        interaction.future.set_result("Confirmed, good work")
        assert future.result() == "Confirmed, good work"

        _mod._broker = None
```

- [ ] **Step 2: Run test, verify it passes** (this tests the data flow, not the vessel integration)

- [ ] **Step 3: Modify vessel.py project completion check**

In `_on_child_complete_inner`, replace the block at line ~2327-2344 that calls `_request_ceo_confirmation`. Instead, create a project_confirm node in the tree assigned to CEO_ID, and schedule it via `schedule_node`. CeoExecutor will handle the rest.

```python
# Replace the _request_ceo_confirmation call with:
if node.node_type not in SKIP_COMPLETION_TYPES and tree.is_project_complete():
    ea_node = tree.get_ea_node()
    logger.info("[PROJECT COMPLETE] EA node {} done — scheduling CEO confirmation", ea_node.id)

    ea_parent = tree.get_node(ea_node.parent_id) if ea_node.parent_id else None
    if ea_parent and ea_parent.is_ceo_node:
        if ea_parent.status != TaskPhase.COMPLETED.value:
            if ea_parent.status == TaskPhase.PENDING.value:
                ea_parent.set_status(TaskPhase.PROCESSING)
            ea_parent.set_status(TaskPhase.COMPLETED)
        save_tree_async(entry.tree_path)

    # Build confirmation summary
    _pdir = ea_node.project_dir or str(Path(entry.tree_path).parent)
    ea_node.load_content(_pdir)
    children = [c for c in tree.get_children(ea_node.id) if not c.is_ceo_node]
    lines = [f"Project Completion Report — {ea_node.description}", ""]
    for i, child in enumerate(children, 1):
        child.load_content(_pdir)
        status_icon = "✓" if child.status == TaskPhase.ACCEPTED else "●"
        lines.append(f"{status_icon} Subtask {i} ({child.employee_id}): {child.title or child.description}")
        lines.append(f"  Result: {child.result or 'None'}")
        lines.append("")
    lines.append("Please confirm project completion or provide feedback.")
    confirm_desc = "\n".join(lines)

    # Create confirm node assigned to CEO
    from onemancompany.core.config import CEO_ID
    confirm_node = tree.add_child(
        parent_id=ea_node.id,
        employee_id=CEO_ID,
        description=confirm_desc,
        acceptance_criteria=[],
    )
    confirm_node.node_type = NodeType.CEO_REQUEST
    confirm_node.project_id = project_id
    confirm_node.project_dir = _pdir
    save_tree_async(entry.tree_path)

    self.schedule_node(CEO_ID, confirm_node.id, entry.tree_path)
    self._schedule_next(CEO_ID)
```

- [ ] **Step 4: Handle CEO's confirm response in _execute_task completion**

After CeoExecutor returns (CEO replied), the confirm node goes COMPLETED → ACCEPTED → FINISHED (system node). Then `_on_child_complete_inner` fires for the parent (EA node). Since all children are resolved, the existing Gate 1 auto-completion + `_full_cleanup` path handles the rest.

Modify `_full_cleanup` to check if the CEO's confirm response indicates rejection (parse CEO text for negative signals) or acceptance (default).

- [ ] **Step 5: Remove old code from vessel.py**

Delete:
- `CEO_REPORT_CONFIRM_DELAY` constant
- `_pending_ceo_reports` dict
- `_request_ceo_confirmation` method
- `_ceo_report_auto_confirm` method
- Keep `_confirm_ceo_report` but simplify — it's now called from the normal task completion path, not from a timer

- [ ] **Step 6: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -x -q`
Fix any broken tests that relied on old _request_ceo_confirmation behavior.

- [ ] **Step 7: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_ceo_broker.py
git commit -m "feat(ceo-executor): project completion uses CeoExecutor, remove auto-confirm timer"
```

---

### Task 7: API endpoints — unified CEO session

**Files:**
- Modify: `src/onemancompany/api/routes.py`
- Test: `tests/unit/api/test_routes.py` (or new test file)

New endpoints:
- `GET /api/ceo/sessions` — list sessions (for project list UI)
- `GET /api/ceo/sessions/{project_id}` — get session history
- `POST /api/ceo/sessions/{project_id}/message` — CEO sends message
- `POST /api/ceo/sessions/new` — CEO creates new task (replaces /api/ceo/task)

Old endpoints to mark deprecated / remove in Task 9.

- [ ] **Step 1: Write failing tests for new endpoints**

```python
# tests/unit/api/test_ceo_sessions.py
"""Tests for unified CEO session endpoints."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestListSessions:
    def test_returns_session_list(self, client):
        with patch("onemancompany.core.ceo_broker.get_ceo_broker") as mock:
            broker = MagicMock()
            broker.list_sessions.return_value = [
                {"project_id": "p1", "has_pending": True, "pending_count": 1},
            ]
            mock.return_value = broker
            resp = client.get("/api/ceo/sessions")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["sessions"]) == 1


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_resolves_pending(self):
        # POST /api/ceo/sessions/{project_id}/message with text
        # Assert pending interaction resolved
        pass  # Fill with actual test using test client
```

- [ ] **Step 2: Implement endpoints**

```python
# In routes.py, add new endpoints:

@router.get("/api/ceo/sessions")
async def list_ceo_sessions():
    from onemancompany.core.ceo_broker import get_ceo_broker
    broker = get_ceo_broker()
    return {"sessions": broker.list_sessions()}


@router.get("/api/ceo/sessions/{project_id:path}")
async def get_ceo_session(project_id: str):
    from onemancompany.core.ceo_broker import get_ceo_broker
    broker = get_ceo_broker()
    session = broker.get_session(project_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"project_id": project_id, "history": session.history, "has_pending": session.has_pending}


@router.post("/api/ceo/sessions/{project_id:path}/message")
async def send_ceo_session_message(project_id: str, body: dict):
    from onemancompany.core.ceo_broker import get_ceo_broker
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    broker = get_ceo_broker()
    result = await broker.handle_input(project_id, text)

    if result["type"] == "followup":
        # Dispatch as CEO_FOLLOWUP in the project tree
        # Reuse existing continuation logic from routes.py
        pass  # Wire to existing _dispatch_ceo_followup

    return result


@router.post("/api/ceo/sessions/new")
async def create_ceo_session(
    task: str = Form(""),
    mode: str = Form("standard"),
    files: list[UploadFile] = File(default=[]),
):
    """CEO creates a new task — reuses existing project creation logic."""
    # Reuse logic from current /api/ceo/task endpoint
    # After project creation, broker.get_or_create_session(project_id)
    pass  # Wire to existing ceo_submit_task internals
```

- [ ] **Step 3: Run tests, then full suite**

- [ ] **Step 4: Commit**

```bash
git add src/onemancompany/api/routes.py tests/unit/api/test_ceo_sessions.py
git commit -m "feat(ceo-executor): unified CEO session API endpoints"
```

---

### Task 8: Restart recovery — CeoBroker.recover()

**Files:**
- Modify: `src/onemancompany/core/ceo_broker.py`
- Modify: `src/onemancompany/core/task_persistence.py`
- Test: `tests/unit/core/test_ceo_broker.py`

- [ ] **Step 1: Write failing test**

```python
class TestCeoBrokerRecovery:
    def test_recover_rebuilds_pending_from_trees(self):
        """On restart, recover pending CEO_REQUEST nodes into sessions."""
        from onemancompany.core.ceo_broker import CeoBroker
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import NodeType, TaskPhase
        import tempfile

        tree = TaskTree(project_id="proj_001/iter_001")
        root = tree.create_root("00001", "CEO task")
        root.node_type = NodeType.CEO_PROMPT.value

        ea = tree.add_child(root.id, "00004", "EA dispatch", [])
        ea.node_type = NodeType.TASK.value
        ea.set_status(TaskPhase.PROCESSING)
        ea.set_status(TaskPhase.HOLDING)

        # Pending CEO_REQUEST — should be recovered
        req = tree.add_child(ea.id, "00001", "Need CEO approval", [])
        req.node_type = NodeType.CEO_REQUEST.value
        # PENDING — hasn't been executed yet

        with tempfile.TemporaryDirectory() as tmpdir:
            tree_path = Path(tmpdir) / "iter_001" / "task_tree.yaml"
            tree_path.parent.mkdir(parents=True)
            tree.save(tree_path)

            broker = CeoBroker()
            broker.recover(Path(tmpdir))

            session = broker.get_session("proj_001/iter_001")
            assert session is not None
            # The PENDING CEO_REQUEST should have been noted
            # (actual pending Futures are created when schedule_node runs)
```

- [ ] **Step 2: Implement CeoBroker.recover()**

```python
def recover(self, projects_dir: Path) -> None:
    """Rebuild sessions from disk on restart.

    Scans project trees for:
    - PENDING/PROCESSING CEO_REQUEST nodes → schedule via employee_manager
    - is_project_complete() with CEO_PROMPT at COMPLETED → schedule confirm
    Also loads conversation history from ceo_session.yaml files.
    """
    from onemancompany.core.config import TASK_TREE_FILENAME
    from onemancompany.core.task_lifecycle import NodeType, TaskPhase
    from onemancompany.core.task_tree import get_tree

    if not projects_dir.exists():
        return

    for tree_path in projects_dir.rglob(TASK_TREE_FILENAME):
        try:
            tree = get_tree(tree_path)
        except Exception:
            continue

        project_id = tree.project_id
        project_dir = tree_path.parent

        # Load existing conversation history
        session = self.get_or_create_session(project_id)
        session.load_history(project_dir)

        logger.debug("[CeoBroker] Recovered session for project={}", project_id)
```

- [ ] **Step 3: Wire recovery into startup**

In `task_persistence.py` `recover_schedule_from_trees`, add at the end:

```python
# Recover CeoBroker sessions
from onemancompany.core.ceo_broker import get_ceo_broker
broker = get_ceo_broker()
broker.recover(projects_dir)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_ceo_broker.py -v`
Run: `.venv/bin/python -m pytest tests/unit/ -x -q`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/ceo_broker.py src/onemancompany/core/task_persistence.py tests/unit/core/test_ceo_broker.py
git commit -m "feat(ceo-executor): CeoBroker restart recovery from trees"
```

---

### Task 9: Frontend — project list + conversation panel

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/style.css`
- Modify: `frontend/index.html` (if layout changes needed)

- [ ] **Step 1: Add project list panel in left column**

In `app.js`, add a `CeoSessionPanel` class that:
- Fetches `GET /api/ceo/sessions` on init and on `ceo_session_message` WebSocket events
- Renders project list with pending indicators (`●`)
- Clicking a project loads its session history and shows conversation panel
- "New Task" button opens task creation dialog (reuse existing)

```javascript
class CeoSessionPanel {
    constructor(container) {
        this._container = container;
        this._currentProjectId = null;
        this._sessions = [];
    }

    async refresh() {
        const resp = await fetch('/api/ceo/sessions');
        const data = await resp.json();
        this._sessions = data.sessions;
        this._render();
    }

    _render() {
        // Left panel: project list
        // Right panel: conversation for selected project
    }

    async selectProject(projectId) {
        this._currentProjectId = projectId;
        const resp = await fetch(`/api/ceo/sessions/${encodeURIComponent(projectId)}`);
        const data = await resp.json();
        this._renderConversation(data.history, data.has_pending);
    }

    async sendMessage(text) {
        if (!this._currentProjectId) return;
        const resp = await fetch(`/api/ceo/sessions/${encodeURIComponent(this._currentProjectId)}/message`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text}),
        });
        const result = await resp.json();
        await this.selectProject(this._currentProjectId);
        return result;
    }
}
```

- [ ] **Step 2: Add WebSocket listener for CEO_SESSION_MESSAGE**

```javascript
// In the WebSocket message handler:
if (msg.type === 'ceo_session_message') {
    if (this._ceoSessionPanel) {
        this._ceoSessionPanel.refresh();
        // If currently viewing this project, append message
        if (this._ceoSessionPanel._currentProjectId === msg.payload.project_id) {
            this._ceoSessionPanel.selectProject(msg.payload.project_id);
        }
    }
    return;
}
```

- [ ] **Step 3: Replace old CEO inbox UI**

Remove or hide the old inbox panel rendering. The new CeoSessionPanel replaces it.

- [ ] **Step 4: Test manually in browser**

- Open the app
- Verify project list appears in left panel
- Verify clicking a project shows conversation history
- Verify sending a message works
- Verify pending indicators show correctly

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js frontend/style.css frontend/index.html
git commit -m "feat(ceo-executor): frontend per-project conversation TUI"
```

---

### Task 10: Remove old code — cleanup

**Files:**
- Delete: `src/onemancompany/core/ceo_conversation.py` (after verifying nothing imports it)
- Modify: `src/onemancompany/api/routes.py` (remove old CEO endpoints)
- Modify: `src/onemancompany/core/vessel.py` (remove old confirmation code)
- Modify: `src/onemancompany/core/conversation_hooks.py` (remove _close_ceo_inbox)
- Modify: `src/onemancompany/core/models.py` (remove CEO_REPORT, CEO_INBOX_UPDATED if unused)
- Test: Run full suite

- [ ] **Step 1: Identify all imports of ceo_conversation**

```bash
grep -r "ceo_conversation" src/ --include="*.py"
```

Remove all imports and usages.

- [ ] **Step 2: Delete ceo_conversation.py**

```bash
git rm src/onemancompany/core/ceo_conversation.py
```

- [ ] **Step 3: Remove old CEO endpoints from routes.py**

Delete:
- `GET /api/ceo/inbox`
- `POST /api/ceo/inbox/{node_id}/open`
- `POST /api/ceo/inbox/{node_id}/message`
- `POST /api/ceo/inbox/{node_id}/complete`
- `POST /api/ceo/inbox/{node_id}/confirm`
- `POST /api/ceo/inbox/{node_id}/dismiss`
- `POST /api/ceo/inbox/{node_id}/upload`
- `POST /api/ceo/inbox/{node_id}/ea-auto-reply`
- `POST /api/ceo/report/{project_id}/confirm`
- Helper functions: `_scan_ceo_inbox_nodes`, `_complete_ceo_request`, `_ea_analyze_and_settle`, `_ceo_request_locks`

Keep `POST /api/ceo/task` as redirect to new endpoint (or remove if fully replaced).

- [ ] **Step 4: Remove old vessel.py confirmation code**

Delete:
- `_pending_ceo_reports` dict initialization
- `CEO_REPORT_CONFIRM_DELAY` constant
- `_request_ceo_confirmation` method
- `_ceo_report_auto_confirm` method
- Simplify `_confirm_ceo_report` — now only advances CEO_PROMPT node status

- [ ] **Step 5: Remove _close_ceo_inbox hook**

In `conversation_hooks.py`, remove the `_close_ceo_inbox` function.

- [ ] **Step 6: Clean up EventTypes**

In `models.py`:
- Remove `CEO_INBOX_UPDATED` if no longer used
- Remove `CEO_REPORT` if no longer used
- Keep `CEO_TASK_SUBMITTED` (still useful for activity log)
- Keep `CEO_SESSION_MESSAGE` (new)

- [ ] **Step 7: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -x -q`
Fix any broken tests.

- [ ] **Step 8: Verify no dead imports**

```bash
.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"
.venv/bin/python -c "from onemancompany.core.vessel import employee_manager; print('OK')"
```

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(ceo-executor): remove old CEO conversation, inbox, and auto-confirm code"
```

---

### Task 11: End-to-end verification + PR

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/python -m pytest tests/unit/ -x -q
```

- [ ] **Step 2: Check audit doc completeness**

Open `docs/superpowers/specs/2026-03-29-ceo-executor-audit.md` and verify every `[ ]` has been addressed.

- [ ] **Step 3: Create PR**

```bash
git push -u origin feat/ceo-executor
gh pr create --title "feat: CEO unified conversation model with CeoExecutor" --body "..."
```

- [ ] **Step 4: Request code review**

Dispatch superpowers:code-reviewer subagent.

- [ ] **Step 5: Fix review issues**

- [ ] **Step 6: Wait for CEO to say merge**
