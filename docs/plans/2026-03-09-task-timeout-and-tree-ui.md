# Task Timeout + Tree UI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **IMPORTANT:** Read `AI_CONTRIBUTING.md` before writing ANY code. Follow its rules exactly: systematic design, registry pattern, no silent exceptions, TDD, mock at importing module level, loguru logger, lazy imports for circular deps.

**Goal:** Add robust task timeout with subprocess execution (cancel = kill process) and a family-tree-style task tree visualization as the project detail view.

**Architecture:** Replace LangChainExecutor with SubprocessExecutor that runs `launch.sh` per employee. TaskNode gains `timeout_seconds`. Frontend project modal replaced with D3.js tree + detail drawer. Backend adds TaskTreeManager with FIFO queue for concurrent safety + real-time WebSocket push.

**Tech Stack:** Python asyncio subprocess, MCP stdio, D3.js (tree layout + zoom/pan), Canvas 2D (node avatars), WebSocket incremental events.

**Key reference files:**
- Design doc: `docs/plans/2026-03-09-task-timeout-and-tree-ui-design.md`
- Coding guide: `AI_CONTRIBUTING.md`
- Vessel executors: `src/onemancompany/core/vessel.py` (Launcher:207, LangChainExecutor:231, ScriptExecutor:289, _execute_task:731)
- Task tree: `src/onemancompany/core/task_tree.py` (TaskNode:20)
- Tree tools: `src/onemancompany/agents/tree_tools.py` (dispatch_child:44)
- MCP server: `src/onemancompany/tools/mcp/server.py` (main:105)
- VesselConfig: `src/onemancompany/core/vessel_config.py` (LimitsConfig:59)
- Frontend: `frontend/app.js` (loadProjectDetail:2527), `frontend/index.html` (project-modal:473), `frontend/style.css` (project-modal:1441)
- WebSocket: `src/onemancompany/api/websocket.py` (broadcast:31)
- Pixel art: `frontend/office.js` (drawCharacter:547, palettes:32)

---

### Task 1: Add timeout_seconds to TaskNode + dispatch_child

**Files:**
- Modify: `src/onemancompany/core/task_tree.py`
- Modify: `src/onemancompany/agents/tree_tools.py`
- Modify: `tests/unit/core/test_task_tree.py`
- Modify: `tests/unit/agents/test_tree_tools.py`

**Step 1: Write the failing tests**

Add to `tests/unit/core/test_task_tree.py`:

```python
def test_task_node_default_timeout(self):
    node = TaskNode()
    assert node.timeout_seconds == 3600

def test_task_node_custom_timeout(self):
    node = TaskNode(timeout_seconds=600)
    assert node.timeout_seconds == 600

def test_timeout_in_to_dict(self):
    node = TaskNode(timeout_seconds=1800)
    d = node.to_dict()
    assert d["timeout_seconds"] == 1800

def test_timeout_in_from_dict(self):
    node = TaskNode.from_dict({"timeout_seconds": 900})
    assert node.timeout_seconds == 900

def test_add_child_with_timeout(self):
    tree = TaskTree(project_id="proj1")
    root = tree.create_root("00001", "Root")
    child = tree.add_child(root.id, "00010", "Work", ["done"], timeout_seconds=1200)
    assert child.timeout_seconds == 1200
```

Add to `tests/unit/agents/test_tree_tools.py`:

```python
def test_dispatch_child_with_timeout(self):
    """dispatch_child passes timeout_seconds to child node."""
    from onemancompany.agents.tree_tools import dispatch_child

    tree = _make_tree_with_root()
    tree.task_id_map["task-t1"] = tree.root_id

    vessel, task = _make_vessel_and_task()
    tok_v, tok_t = _set_context(vessel, "task-t1")

    mock_handle = MagicMock()
    mock_agent_task = MagicMock()
    mock_agent_task.id = "child-t1"
    mock_handle.push_task.return_value = mock_agent_task
    mock_em = MagicMock()
    mock_em.get_handle.return_value = mock_handle
    mock_cs = MagicMock()
    mock_cs.employees = {"00100": MagicMock()}

    try:
        with (
            patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
            patch("onemancompany.agents.tree_tools._save_tree"),
            patch("onemancompany.core.state.company_state", mock_cs),
            patch("onemancompany.core.vessel.employee_manager", mock_em),
        ):
            result = dispatch_child.invoke({
                "employee_id": "00100",
                "description": "build X",
                "acceptance_criteria": ["works"],
                "timeout_seconds": 1800,
            })

        assert result["status"] == "dispatched"
        child_node = tree.get_node(result["node_id"])
        assert child_node.timeout_seconds == 1800
    finally:
        _reset_context(tok_v, tok_t)
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py tests/unit/agents/test_tree_tools.py -v`
Expected: FAIL (timeout_seconds field doesn't exist)

**Step 3: Implement**

In `src/onemancompany/core/task_tree.py`:
- Add `timeout_seconds: int = 3600` to `TaskNode` dataclass (after `output_tokens`)
- Add `"timeout_seconds": self.timeout_seconds` to `to_dict()`
- Add `timeout_seconds: int = 3600` param to `TaskTree.add_child()`, pass to `TaskNode()`

In `src/onemancompany/agents/tree_tools.py`:
- Add `timeout_seconds: int = 3600` param to `dispatch_child` function signature and docstring
- Pass `timeout_seconds=timeout_seconds` when calling `tree.add_child()`

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py tests/unit/agents/test_tree_tools.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/task_tree.py src/onemancompany/agents/tree_tools.py tests/unit/core/test_task_tree.py tests/unit/agents/test_tree_tools.py
git commit -m "feat: add timeout_seconds to TaskNode and dispatch_child"
```

---

### Task 2: Create SubprocessExecutor with two-stage kill

**Files:**
- Create: `src/onemancompany/core/subprocess_executor.py`
- Create: `tests/unit/core/test_subprocess_executor.py`

**Step 1: Write the failing tests**

```python
# tests/unit/core/test_subprocess_executor.py
"""Tests for SubprocessExecutor — subprocess-based task execution with two-stage kill."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.vessel import LaunchResult, TaskContext


class TestSubprocessExecutor:
    @pytest.mark.asyncio
    async def test_execute_happy_path(self):
        """Execute runs launch.sh and captures output."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b'{"output":"done","model":"test","input_tokens":10,"output_tokens":5}', b"")
        mock_proc.returncode = 0
        mock_proc.pid = 12345

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await exe.execute("do work", ctx)

        assert result.output == "done"
        assert result.model_used == "test"
        assert result.input_tokens == 10

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        """Timeout triggers two-stage kill."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh", timeout_seconds=1)

        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(TimeoutError, match="Timeout"):
                await exe.execute("do work", ctx)

        mock_proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_terminates_process(self):
        """cancel() sends SIGTERM, polls, then SIGKILL if needed."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.poll = MagicMock(return_value=None)  # never exits
        mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError())
        exe._process = mock_proc

        await exe.cancel()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_subprocess_executor.py -v`
Expected: FAIL (module doesn't exist)

**Step 3: Implement SubprocessExecutor**

```python
# src/onemancompany/core/subprocess_executor.py
"""SubprocessExecutor — runs employee tasks as bash subprocesses.

Each company-hosted employee runs via launch.sh. Cancel = OS-level kill.
Replaces LangChainExecutor and ScriptExecutor.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Callable

from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR
from onemancompany.core.vessel import Launcher, LaunchResult, TaskContext

# Two-stage kill: SIGTERM → poll every 5s → SIGKILL after 30s
_KILL_POLL_INTERVAL = 5
_KILL_GRACE_PERIOD = 30


class SubprocessExecutor(Launcher):
    """Execute employee tasks via bash subprocess with OS-level cancel."""

    def __init__(
        self,
        employee_id: str,
        script_path: str = "",
        timeout_seconds: int = 3600,
    ) -> None:
        self.employee_id = employee_id
        self.script_path = script_path or str(EMPLOYEES_DIR / employee_id / "launch.sh")
        self.timeout_seconds = timeout_seconds
        self._process: asyncio.subprocess.Process | None = None

    async def execute(
        self,
        task_description: str,
        context: TaskContext,
        on_log: Callable[[str, str], None] | None = None,
    ) -> LaunchResult:
        env = {
            **os.environ,
            "OMC_EMPLOYEE_ID": context.employee_id,
            "OMC_TASK_ID": context.task_id,
            "OMC_PROJECT_ID": context.project_id,
            "OMC_PROJECT_DIR": context.work_dir,
            "OMC_TASK_DESCRIPTION": task_description,
            "OMC_SERVER_URL": f"http://localhost:{os.environ.get('OMC_PORT', '8000')}",
        }

        cwd = context.work_dir or str(EMPLOYEES_DIR / self.employee_id)

        self._process = await asyncio.create_subprocess_exec(
            "bash", self.script_path, str(EMPLOYEES_DIR / self.employee_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        if on_log:
            on_log("start", f"Started subprocess PID={self._process.pid}")

        try:
            stdout, stderr = await asyncio.wait_for(
                self._process.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Task timeout after {}s for employee {} (PID={})",
                self.timeout_seconds, self.employee_id, self._process.pid,
            )
            await self.cancel()
            raise TimeoutError(f"Timeout after {self.timeout_seconds}s") from None

        if on_log and stderr:
            on_log("stderr", stderr.decode(errors="replace")[:2000])

        if self._process.returncode != 0:
            err_msg = stderr.decode(errors="replace")[:500] if stderr else "Unknown error"
            if on_log:
                on_log("error", f"Exit code {self._process.returncode}: {err_msg}")
            return LaunchResult(output=f"Error (exit {self._process.returncode}): {err_msg}")

        # Parse JSON output from launch.sh
        raw = stdout.decode(errors="replace").strip()
        try:
            data = json.loads(raw)
            return LaunchResult(
                output=data.get("output", raw),
                model_used=data.get("model", ""),
                input_tokens=data.get("input_tokens", 0),
                output_tokens=data.get("output_tokens", 0),
                total_tokens=data.get("input_tokens", 0) + data.get("output_tokens", 0),
            )
        except (json.JSONDecodeError, AttributeError):
            return LaunchResult(output=raw)

    async def cancel(self) -> None:
        """Two-stage kill: SIGTERM → poll every 5s → SIGKILL after 30s."""
        proc = self._process
        if proc is None or proc.returncode is not None:
            return

        logger.info("Cancelling subprocess PID={} for {}", proc.pid, self.employee_id)
        proc.terminate()

        elapsed = 0
        while elapsed < _KILL_GRACE_PERIOD:
            try:
                await asyncio.wait_for(proc.wait(), timeout=_KILL_POLL_INTERVAL)
                logger.info("Process PID={} exited gracefully after {}s", proc.pid, elapsed)
                return
            except asyncio.TimeoutError:
                elapsed += _KILL_POLL_INTERVAL
                logger.debug("Process PID={} still alive after {}s", proc.pid, elapsed)

        logger.warning("Process PID={} did not exit after {}s — sending SIGKILL", proc.pid, _KILL_GRACE_PERIOD)
        proc.kill()
        await proc.wait()
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_subprocess_executor.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/subprocess_executor.py tests/unit/core/test_subprocess_executor.py
git commit -m "feat: add SubprocessExecutor with two-stage kill and timeout"
```

---

### Task 3: Create launch.sh template for company-hosted employees

**Files:**
- Create: `company/assets/tools/launch_template.sh`

**Step 1: Write launch.sh template**

This script follows the ralph.sh pattern: loop calling LLM via OpenRouter, handle tool calls via MCP stdio.

```bash
#!/usr/bin/env bash
# launch.sh — Company-hosted employee agent loop.
#
# Convention:
#   $1 = employee_dir (contains profile.yaml, vessel/)
#   Environment vars: OMC_EMPLOYEE_ID, OMC_TASK_ID, OMC_PROJECT_ID,
#                     OMC_PROJECT_DIR, OMC_TASK_DESCRIPTION, OMC_SERVER_URL
#
# Outputs JSON to stdout: {"output":"...", "model":"...", "input_tokens":N, "output_tokens":N}
# All logging goes to stderr.

set -euo pipefail

EMPLOYEE_DIR="${1:?Usage: launch.sh <employee_dir>}"
EMPLOYEE_DIR="$(cd "$EMPLOYEE_DIR" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MAX_ITERATIONS="${OMC_MAX_ITERATIONS:-20}"

# Start MCP server for tool calls
MCP_SERVER_PID=""
cleanup() {
    if [ -n "$MCP_SERVER_PID" ] && kill -0 "$MCP_SERVER_PID" 2>/dev/null; then
        kill "$MCP_SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# Launch MCP server as coprocess (stdin/stdout for JSON-RPC)
coproc MCP_SERVER {
    exec python -m onemancompany.tools.mcp.server 2>/dev/null
}
MCP_SERVER_PID=$MCP_SERVER_PID

>&2 echo "[launch.sh] Started for employee ${OMC_EMPLOYEE_ID} (PID=$$)"
>&2 echo "[launch.sh] Task: ${OMC_TASK_DESCRIPTION:0:100}"
>&2 echo "[launch.sh] MCP server PID=${MCP_SERVER_PID}"

# Agent loop — call LLM, handle tool calls, repeat until done
# Implementation depends on the LLM calling mechanism (OpenRouter API via curl/python)
# This template delegates to a Python agent runner script
python -m onemancompany.agents.agent_runner \
    --employee-id "${OMC_EMPLOYEE_ID}" \
    --task-id "${OMC_TASK_ID}" \
    --project-id "${OMC_PROJECT_ID}" \
    --project-dir "${OMC_PROJECT_DIR}" \
    --max-iterations "${MAX_ITERATIONS}" \
    --mcp-fd-in "${MCP_SERVER[0]}" \
    --mcp-fd-out "${MCP_SERVER[1]}" \
    <<< "${OMC_TASK_DESCRIPTION}"
```

Note: The actual agent_runner Python module (LLM loop + MCP tool call handling) is a separate task. This template establishes the shell convention.

**Step 2: Commit**

```bash
git add company/assets/tools/launch_template.sh
git commit -m "feat: add launch.sh template for company-hosted subprocess execution"
```

---

### Task 4: Wire timeout into _execute_task

**Files:**
- Modify: `src/onemancompany/core/vessel.py`
- Modify: `tests/unit/core/test_agent_loop.py`

**Step 1: Write the failing tests**

Add to `tests/unit/core/test_agent_loop.py`:

```python
class TestTaskTimeout:
    """Tests for task timeout via TaskNode.timeout_seconds."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_timeout_marks_task_failed(self, mock_bus, mock_state):
        """When executor raises TimeoutError, task is marked FAILED."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {"00010": MagicMock(current_task_summary="")}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        mock_executor = AsyncMock(spec=Launcher)
        mock_executor.execute.side_effect = TimeoutError("Timeout after 60s")
        mock_executor.is_ready.return_value = True
        mgr.register("00010", mock_executor)

        task = AgentTask(id="t1", description="slow work", project_dir="/tmp/proj")
        mgr.boards["00010"].tasks.append(task)

        with patch("onemancompany.core.vessel._load_project_tree", return_value=None), \
             patch("onemancompany.core.vessel._save_project_tree"):
            await mgr._execute_task("00010", task)

        assert task.status == TaskPhase.FAILED
        assert "Timeout" in task.result
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_agent_loop.py::TestTaskTimeout -v`
Expected: FAIL (TimeoutError not caught in _execute_task)

**Step 3: Implement**

In `src/onemancompany/core/vessel.py`, in `_execute_task`, add `TimeoutError` to the exception handling after `asyncio.CancelledError` (around line 864):

```python
        except TimeoutError as te:
            agent_error = True
            task.status = TaskPhase.FAILED
            task.result = str(te)
            if not task.completed_at:
                task.completed_at = datetime.now().isoformat()
            self._log(employee_id, task, "timeout", f"Task timed out: {te!s}")
            await event_bus.publish(
                CompanyEvent(
                    type="agent_done",
                    payload={"role": role, "summary": f"Timeout: {te!s}"},
                    agent=role,
                )
            )
```

Also: when looking up timeout for executor, read from the TaskNode if available. In the executor call section, if the executor is a `SubprocessExecutor`, set its `timeout_seconds` from the tree node:

```python
        # Set timeout from task tree node if available
        if task.project_dir:
            tree = _load_project_tree(task.project_dir)
            if tree:
                node_id = _node_id_for_task(tree, task.id)
                if node_id:
                    node = tree.get_node(node_id)
                    if node and node.timeout_seconds:
                        from onemancompany.core.subprocess_executor import SubprocessExecutor
                        if isinstance(executor, SubprocessExecutor):
                            executor.timeout_seconds = node.timeout_seconds
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_agent_loop.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_agent_loop.py
git commit -m "feat: wire task timeout into _execute_task with TimeoutError handling"
```

---

### Task 5: Create TaskTreeManager with FIFO queue

**Files:**
- Create: `src/onemancompany/core/tree_manager.py`
- Create: `tests/unit/core/test_tree_manager.py`

**Step 1: Write the failing tests**

```python
# tests/unit/core/test_tree_manager.py
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
            # Give consumer time to process
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

        # Both processed, in order
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
                node_id=root.id,  # parent
                data={"employee_id": "00010", "description": "new child", "acceptance_criteria": ["done"]},
            ))
            await asyncio.sleep(0.1)
            await mgr.stop()

        children = tree.get_children(root.id)
        assert len(children) == 1
        assert children[0].employee_id == "00010"
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_tree_manager.py -v`
Expected: FAIL (module doesn't exist)

**Step 3: Implement TaskTreeManager**

```python
# src/onemancompany/core/tree_manager.py
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

from onemancompany.core.task_tree import TaskTree


@dataclass
class TreeEvent:
    """A single tree mutation event."""
    type: str          # "node_added" | "node_updated" | "node_accepted" | "node_rejected" | "node_failed"
    node_id: str       # target node (or parent for node_added)
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
        path = Path(self.project_dir) / "task_tree.yaml"
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
            node.status = "accepted"
            node.acceptance_result = {"passed": True, "notes": event.data.get("notes", "")}
        elif event.type == "node_rejected" and node:
            node.acceptance_result = {"passed": False, "notes": event.data.get("reason", "")}
            node.status = "failed" if not event.data.get("retry") else "pending"
        elif event.type == "node_failed" and node:
            node.status = "failed"
            node.result = event.data.get("result", node.result)
        else:
            logger.warning("Unknown tree event type or missing node: {} {}", event.type, event.node_id)
            return

        self._save()
        await self._broadcast(event)

    def _save(self) -> None:
        """Persist tree to disk."""
        if self._tree:
            path = Path(self.project_dir) / "task_tree.yaml"
            self._tree.save(path)

    async def _broadcast(self, event: TreeEvent) -> None:
        """Publish tree update to WebSocket via event bus."""
        from onemancompany.core.events import CompanyEvent, event_bus
        await event_bus.publish(CompanyEvent(
            type="tree_update",
            payload={
                "project_id": self.project_id,
                "event_type": event.type,
                "node_id": event.node_id,
                "data": event.data,
                "timestamp": event.timestamp,
            },
            agent="SYSTEM",
        ))
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_tree_manager.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/tree_manager.py tests/unit/core/test_tree_manager.py
git commit -m "feat: add TaskTreeManager with FIFO queue for concurrent-safe tree updates"
```

---

### Task 6: Add tree API endpoint

**Files:**
- Modify: `src/onemancompany/api/routes.py`
- Modify: `tests/unit/api/test_routes.py`

**Step 1: Write the failing test**

Add to `tests/unit/api/test_routes.py`:

```python
class TestProjectTreeEndpoint:
    @pytest.mark.asyncio
    async def test_get_project_tree(self):
        """GET /api/projects/{id}/tree returns full tree structure."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root task")
        child = tree.add_child(root.id, "00010", "Child task", ["criterion"])
        child.status = "completed"
        child.result = "Done"

        with patch("onemancompany.api.routes._load_project_tree_for_api", return_value=tree):
            async with AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.get("/api/projects/proj1/tree")

        assert resp.status_code == 200
        data = resp.json()
        assert data["root_id"] == root.id
        assert len(data["nodes"]) == 2
        # Check node structure
        root_node = next(n for n in data["nodes"] if n["id"] == root.id)
        assert root_node["employee_id"] == "00001"
        child_node = next(n for n in data["nodes"] if n["id"] == child.id)
        assert child_node["status"] == "completed"

    @pytest.mark.asyncio
    async def test_get_project_tree_not_found(self):
        """Returns 404 when no tree exists."""
        with patch("onemancompany.api.routes._load_project_tree_for_api", return_value=None):
            async with AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.get("/api/projects/proj1/tree")

        assert resp.status_code == 404
```

**Step 2: Implement**

In `src/onemancompany/api/routes.py`, add:

```python
def _load_project_tree_for_api(project_id: str):
    """Load TaskTree for a project, trying known project directories."""
    from onemancompany.core.task_tree import TaskTree
    from onemancompany.core.project_archive import get_project_dir
    project_dir = get_project_dir(project_id)
    if not project_dir:
        return None
    path = Path(project_dir) / "task_tree.yaml"
    if not path.exists():
        return None
    return TaskTree.load(path, project_id=project_id)


@router.get("/api/projects/{project_id}/tree")
async def get_project_tree(project_id: str) -> dict:
    tree = _load_project_tree_for_api(project_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="Task tree not found")
    return {
        "project_id": tree.project_id,
        "root_id": tree.root_id,
        "nodes": [n.to_dict() for n in tree._nodes.values()],
    }
```

**Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/api/test_routes.py::TestProjectTreeEndpoint -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add src/onemancompany/api/routes.py tests/unit/api/test_routes.py
git commit -m "feat: add GET /api/projects/{id}/tree endpoint"
```

---

### Task 7: Frontend — D3.js tree layout with zoom/pan

**Files:**
- Modify: `frontend/index.html` (add D3.js script tag, update project-modal structure)
- Create: `frontend/task-tree.js` (tree rendering + interaction)
- Modify: `frontend/style.css` (tree styles)

**Step 1: Add D3.js and update HTML**

In `frontend/index.html`:
- Add `<script src="https://d3js.org/d3.v7.min.js"></script>` before app.js
- Update `#project-modal` body to:

```html
<div class="modal-body project-tree-layout">
  <div id="project-tree-container" class="project-tree-canvas">
    <svg id="project-tree-svg"></svg>
  </div>
  <div id="project-tree-detail" class="project-tree-drawer hidden">
    <div id="tree-detail-content"></div>
  </div>
</div>
```

**Step 2: Create task-tree.js**

```javascript
// frontend/task-tree.js
// Task tree visualization with D3.js — top-down family tree layout

class TaskTreeRenderer {
    constructor(containerId, detailId) {
        this.containerId = containerId;
        this.detailId = detailId;
        this.svg = null;
        this.g = null;       // transform group for zoom/pan
        this.zoom = null;
        this.treeData = null;
        this.selectedNodeId = null;

        // Node dimensions
        this.nodeWidth = 180;
        this.nodeHeight = 80;
        this.levelSep = 120;   // vertical gap between levels
        this.sibSep = 40;      // horizontal gap between siblings
    }

    // STATUS_COLORS matches pixel art theme
    static STATUS_COLORS = {
        pending: '#666',
        processing: '#4a9eff',
        completed: '#ffaa00',
        accepted: '#00ff88',
        failed: '#ff4444',
        cancelled: '#888',
    };

    async load(projectId) {
        const resp = await fetch(`/api/projects/${projectId}/tree`);
        if (!resp.ok) return;
        this.treeData = await resp.json();
        this.render();
    }

    render() {
        if (!this.treeData || !this.treeData.nodes.length) return;

        const container = document.getElementById(this.containerId);
        const svgEl = container.querySelector('svg');
        const { width, height } = container.getBoundingClientRect();

        // Clear previous
        d3.select(svgEl).selectAll('*').remove();

        this.svg = d3.select(svgEl)
            .attr('width', width)
            .attr('height', height);

        // Zoom + pan
        this.zoom = d3.zoom()
            .scaleExtent([0.3, 3])
            .on('zoom', (event) => {
                this.g.attr('transform', event.transform);
            });
        this.svg.call(this.zoom);

        this.g = this.svg.append('g')
            .attr('transform', `translate(${width / 2}, 40)`);

        // Build hierarchy from flat nodes
        const nodesMap = {};
        this.treeData.nodes.forEach(n => { nodesMap[n.id] = { ...n, children: [] }; });
        this.treeData.nodes.forEach(n => {
            if (n.parent_id && nodesMap[n.parent_id]) {
                nodesMap[n.parent_id].children.push(nodesMap[n.id]);
            }
        });

        const rootData = nodesMap[this.treeData.root_id];
        if (!rootData) return;

        const root = d3.hierarchy(rootData);
        const treeLayout = d3.tree()
            .nodeSize([this.nodeWidth + this.sibSep, this.nodeHeight + this.levelSep]);
        treeLayout(root);

        // Draw links
        this.g.selectAll('.tree-link')
            .data(root.links())
            .enter()
            .append('path')
            .attr('class', 'tree-link')
            .attr('d', d3.linkVertical()
                .x(d => d.x)
                .y(d => d.y));

        // Draw node groups
        const nodeGroups = this.g.selectAll('.tree-node')
            .data(root.descendants())
            .enter()
            .append('g')
            .attr('class', 'tree-node')
            .attr('transform', d => `translate(${d.x}, ${d.y})`)
            .style('cursor', 'pointer')
            .on('click', (event, d) => this.selectNode(d.data));

        // Node card background
        nodeGroups.append('rect')
            .attr('x', -this.nodeWidth / 2)
            .attr('y', -this.nodeHeight / 2)
            .attr('width', this.nodeWidth)
            .attr('height', this.nodeHeight)
            .attr('rx', 6)
            .attr('class', 'tree-node-card');

        // Status indicator bar (left edge)
        nodeGroups.append('rect')
            .attr('x', -this.nodeWidth / 2)
            .attr('y', -this.nodeHeight / 2)
            .attr('width', 4)
            .attr('height', this.nodeHeight)
            .attr('rx', 2)
            .attr('fill', d => TaskTreeRenderer.STATUS_COLORS[d.data.status] || '#666');

        // Employee name
        nodeGroups.append('text')
            .attr('x', -this.nodeWidth / 2 + 14)
            .attr('y', -this.nodeHeight / 2 + 20)
            .attr('class', 'tree-node-name')
            .text(d => d.data.employee_id);

        // Task description (truncated)
        nodeGroups.append('text')
            .attr('x', -this.nodeWidth / 2 + 14)
            .attr('y', -this.nodeHeight / 2 + 40)
            .attr('class', 'tree-node-desc')
            .text(d => (d.data.description || '').substring(0, 25) + ((d.data.description || '').length > 25 ? '...' : ''));

        // Status badge
        nodeGroups.append('text')
            .attr('x', -this.nodeWidth / 2 + 14)
            .attr('y', -this.nodeHeight / 2 + 58)
            .attr('class', 'tree-node-status')
            .attr('fill', d => TaskTreeRenderer.STATUS_COLORS[d.data.status] || '#666')
            .text(d => d.data.status);
    }

    selectNode(nodeData) {
        this.selectedNodeId = nodeData.id;
        const drawer = document.getElementById(this.detailId);
        const content = document.getElementById('tree-detail-content');
        drawer.classList.remove('hidden');

        // Highlight selected node
        this.g.selectAll('.tree-node-card')
            .classed('selected', d => d.data.id === nodeData.id);

        content.innerHTML = this._renderNodeDetail(nodeData);
    }

    _renderNodeDetail(node) {
        const criteria = (node.acceptance_criteria || []).map(c => `<li>${c}</li>`).join('');
        const acceptance = node.acceptance_result
            ? `<div class="detail-section">
                 <h4>Acceptance</h4>
                 <span class="${node.acceptance_result.passed ? 'status-pass' : 'status-fail'}">
                   ${node.acceptance_result.passed ? 'PASSED' : 'FAILED'}
                 </span>
                 <p>${node.acceptance_result.notes || ''}</p>
               </div>`
            : '';

        return `
            <div class="tree-detail-header">
                <div class="tree-detail-avatar" data-employee-id="${node.employee_id}"></div>
                <div>
                    <h3>${node.employee_id}</h3>
                    <span class="tree-detail-status" style="color:${TaskTreeRenderer.STATUS_COLORS[node.status]}">${node.status}</span>
                </div>
            </div>

            <div class="detail-section">
                <h4>Prompt</h4>
                <pre class="detail-prompt">${node.description || '(none)'}</pre>
            </div>

            ${criteria ? `<div class="detail-section"><h4>Acceptance Criteria</h4><ul>${criteria}</ul></div>` : ''}

            <div class="detail-section">
                <h4>Result</h4>
                <pre class="detail-result">${node.result || '(pending)'}</pre>
            </div>

            ${acceptance}

            <div class="detail-section detail-meta">
                <span>Tokens: ${node.input_tokens || 0} in / ${node.output_tokens || 0} out</span>
                <span>Cost: $${(node.cost_usd || 0).toFixed(4)}</span>
                <span>Timeout: ${node.timeout_seconds || 3600}s</span>
            </div>
        `;
    }

    // Real-time update: called on tree_update WebSocket event
    updateNode(nodeId, data) {
        if (!this.treeData) return;
        const node = this.treeData.nodes.find(n => n.id === nodeId);
        if (node) {
            Object.assign(node, data);
            this.render();
            // Re-select if this was the selected node
            if (this.selectedNodeId === nodeId) {
                this.selectNode(node);
            }
        }
    }

    addNode(parentId, nodeData) {
        if (!this.treeData) return;
        this.treeData.nodes.push(nodeData);
        this.render();
    }
}

window.TaskTreeRenderer = TaskTreeRenderer;
```

**Step 3: Add CSS for tree**

Add to `frontend/style.css`:

```css
/* Task Tree */
.project-tree-layout {
    display: flex;
    height: 70vh;
    gap: 0;
}

.project-tree-canvas {
    flex: 7;
    overflow: hidden;
    background: var(--bg-darker, #0a0a0a);
    border-right: 1px solid var(--pixel-green-dim, #1a3a2a);
}

.project-tree-canvas svg {
    width: 100%;
    height: 100%;
}

.tree-link {
    fill: none;
    stroke: var(--pixel-green-dim, #1a3a2a);
    stroke-width: 2;
}

.tree-node-card {
    fill: #1a1a2e;
    stroke: #333;
    stroke-width: 1;
    transition: stroke 0.2s;
}

.tree-node-card.selected {
    stroke: var(--pixel-green, #00ff88);
    stroke-width: 2;
}

.tree-node:hover .tree-node-card {
    stroke: var(--pixel-green, #00ff88);
    stroke-opacity: 0.7;
}

.tree-node-name {
    fill: #fff;
    font-family: var(--font-pixel, monospace);
    font-size: 12px;
    font-weight: bold;
}

.tree-node-desc {
    fill: #aaa;
    font-family: var(--font-pixel, monospace);
    font-size: 10px;
}

.tree-node-status {
    font-family: var(--font-pixel, monospace);
    font-size: 10px;
    text-transform: uppercase;
}

/* Detail Drawer */
.project-tree-drawer {
    flex: 3;
    overflow-y: auto;
    padding: 16px;
    background: #111;
    border-left: 1px solid var(--pixel-green-dim, #1a3a2a);
}

.tree-detail-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid #333;
}

.tree-detail-avatar {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: #222;
    border: 2px solid var(--pixel-green);
}

.detail-section {
    margin-bottom: 16px;
}

.detail-section h4 {
    color: var(--pixel-green);
    font-size: 11px;
    text-transform: uppercase;
    margin-bottom: 4px;
}

.detail-prompt,
.detail-result {
    background: #0a0a0a;
    padding: 8px;
    border-radius: 4px;
    font-size: 12px;
    color: #ccc;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 200px;
    overflow-y: auto;
}

.detail-meta {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 11px;
    color: #666;
}

.status-pass { color: #00ff88; }
.status-fail { color: #ff4444; }
```

**Step 4: Commit**

```bash
git add frontend/index.html frontend/task-tree.js frontend/style.css
git commit -m "feat: add D3.js task tree visualization with zoom/pan and detail drawer"
```

---

### Task 8: Wire frontend — project modal loads tree, WebSocket updates

**Files:**
- Modify: `frontend/app.js`

**Step 1: Update loadProjectDetail to load tree**

Find `loadProjectDetail` (around line 2527) and replace its content to load the tree view:

```javascript
async loadProjectDetail(projectId) {
    const detailEl = document.getElementById('project-detail');
    const contentEl = document.getElementById('project-detail-content');
    detailEl.classList.remove('hidden');

    // Initialize tree renderer if not exists
    if (!this._treeRenderer) {
        this._treeRenderer = new TaskTreeRenderer('project-tree-container', 'project-tree-detail');
    }

    await this._treeRenderer.load(projectId);
    this._currentTreeProjectId = projectId;
}
```

**Step 2: Handle tree_update WebSocket events**

In `handleMessage` (around line 81), add handler for `tree_update`:

```javascript
if (msg.type === 'tree_update') {
    const payload = msg.payload;
    if (this._treeRenderer && this._currentTreeProjectId === payload.project_id) {
        if (payload.event_type === 'node_added') {
            this._treeRenderer.addNode(payload.node_id, payload.data);
        } else {
            this._treeRenderer.updateNode(payload.node_id, payload.data);
        }
    }
}
```

**Step 3: Add script tag for task-tree.js**

In `frontend/index.html`, add before `app.js`:
```html
<script src="task-tree.js"></script>
```

**Step 4: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat: wire project modal to tree renderer + WebSocket real-time updates"
```

---

### Task 9: Final integration test + full suite

**Step 1: Verify compilation**

```bash
.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"
.venv/bin/python -c "from onemancompany.core.subprocess_executor import SubprocessExecutor; print('OK')"
.venv/bin/python -c "from onemancompany.core.tree_manager import TaskTreeManager; print('OK')"
```

**Step 2: Run full test suite**

```bash
.venv/bin/python -m pytest tests/unit/ -x -q
```

Expected: ALL PASS

**Step 3: Fix any failures**

**Step 4: Commit**

```bash
git add -A
git commit -m "test: final integration verification for task timeout + tree UI"
```
