# Human Playtester Talent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a company-hosted "Human Bridge" talent that sends tasks to a human via Gmail, enters HOLDING, polls for replies via cron, and feeds reply content back as task result.

**Architecture:** Three layers: (1) Talent files (profile.yaml, manifest.json, skills, tools) define the human bridge role; (2) HOLDING mechanism in vessel.py detects `__HOLDING:key=value` prefix in agent result, sets task to HOLDING and starts a reply poller cron; (3) `resume_held_task()` on EmployeeManager transitions HOLDING→COMPLETE when reply arrives, triggering task tree callbacks. The polling cron fires on configurable interval (default 1m) and dispatches `[reply_poll]` tasks to the same employee.

**Tech Stack:** Python, LangChain, YAML/JSON config, Gmail tool (existing), cron system (automation.py)

---

### Task 1: Talent Files

Create the human_playtester talent template in the talent market.

**Files:**
- Create: `src/onemancompany/talent_market/talents/human_playtester/profile.yaml`
- Create: `src/onemancompany/talent_market/talents/human_playtester/manifest.json`
- Create: `src/onemancompany/talent_market/talents/human_playtester/tools/manifest.yaml`
- Create: `src/onemancompany/talent_market/talents/human_playtester/skills/playtester/SKILL.md`

**Step 1: Create profile.yaml**

```yaml
id: human_playtester
name: Human Bridge
description: >
  人类桥梁代理 — 通过Gmail向指定人类发送任务邮件，等待人类回复，
  并将回复内容作为任务结果返回给上游。支持配置目标邮箱和轮询间隔。
role: Assistant
remote: false
api_provider: openrouter
llm_model: ''
temperature: 0.3
hosting: company
auth_method: api_key
hiring_fee: 0.2
salary_per_1m_tokens: 0.0
skills:
- playtester
personality_tags:
- reliable
- precise
- bridge
system_prompt_template: >
  You are a Human Bridge agent. Your job is to relay tasks to a human via email
  and collect their responses. You have access to Gmail tools for sending and
  reading emails, plus file tools for workspace operations.

  When you receive a task:
  1. Read your target_email from your manifest config
  2. Compose a clear, professional email describing what the human needs to do
  3. Send the email via Gmail
  4. Return your result with the __HOLDING: prefix, including the thread_id

  When you receive a [reply_poll] task:
  1. Read the Gmail thread referenced in the task
  2. If the human has replied, extract the reply content and call resume_held_task
  3. If no reply yet, respond with "no reply yet"

  When you receive a [cron:reply_*] task:
  1. This is a polling task — check for replies on the referenced thread
  2. If reply found, call resume_held_task with the reply content, then stop the cron
  3. If no reply, respond briefly "no reply yet"
```

**Step 2: Create manifest.json**

```json
{
  "id": "human_bridge",
  "title": "Human Bridge",
  "sections": [
    {
      "title": "Communication Settings",
      "fields": [
        {
          "key": "target_email",
          "type": "text",
          "label": "Human Email Address",
          "placeholder": "human@example.com"
        },
        {
          "key": "polling_interval",
          "type": "text",
          "label": "Reply Polling Interval",
          "default": "1m",
          "placeholder": "1m"
        }
      ]
    }
  ]
}
```

**Step 3: Create tools/manifest.yaml**

```yaml
builtin_tools:
- read_file
- write_file
- list_dir
- bash
custom_tools: []
```

**Step 4: Create skills/playtester/SKILL.md**

The SKILL.md should contain the role prompt for email formatting and reply parsing. Include:
- How to format task emails (clear subject, structured body)
- How to parse reply emails (extract actionable content)
- The `__HOLDING:thread_id=<id>` return format
- How to handle `[reply_poll]` and `[cron:reply_*]` tasks

**Step 5: Add employee to gmail tool.yaml allowed_users**

Modify `company/assets/tools/gmail/tool.yaml` — the employee ID will be dynamic (assigned at hire time), so this step happens during onboarding. Document this requirement in the SKILL.md.

**Step 6: Commit**

```bash
git add src/onemancompany/talent_market/talents/human_playtester/
git commit -m "feat: add human_playtester talent template"
```

---

### Task 2: HOLDING Mechanism in vessel.py

Detect `__HOLDING:key=value,...` prefix in agent result, set task to HOLDING, parse metadata, and start reply poller cron.

**Files:**
- Modify: `src/onemancompany/core/vessel.py:881-888` (section "8. Mark completed")
- Modify: `src/onemancompany/core/vessel.py` (add `_parse_holding_metadata` helper)
- Modify: `src/onemancompany/core/vessel.py` (add `_setup_reply_poller` method)
- Test: `tests/unit/core/test_vessel_holding.py`

**Step 1: Write failing tests**

Create `tests/unit/core/test_vessel_holding.py` with:

```python
"""Tests for HOLDING mechanism in vessel.py."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.vessel import (
    AgentTask,
    EmployeeManager,
    _parse_holding_metadata,
)
from onemancompany.core.task_lifecycle import TaskPhase


class TestParseHoldingMetadata:
    """Test __HOLDING: prefix parsing."""

    def test_basic_holding(self):
        result = "__HOLDING:thread_id=abc123"
        meta = _parse_holding_metadata(result)
        assert meta is not None
        assert meta["thread_id"] == "abc123"

    def test_multiple_keys(self):
        result = "__HOLDING:thread_id=abc,subject=Test Email"
        meta = _parse_holding_metadata(result)
        assert meta["thread_id"] == "abc"
        assert meta["subject"] == "Test Email"

    def test_not_holding(self):
        result = "Task completed successfully"
        meta = _parse_holding_metadata(result)
        assert meta is None

    def test_empty_holding(self):
        result = "__HOLDING:"
        meta = _parse_holding_metadata(result)
        assert meta is not None
        assert meta == {}

    def test_holding_with_trailing_content(self):
        result = "__HOLDING:thread_id=abc\nEmail sent successfully"
        meta = _parse_holding_metadata(result)
        assert meta is not None
        assert meta["thread_id"] == "abc"


class TestHoldingDetection:
    """Test that _execute_task detects HOLDING and transitions correctly."""

    @pytest.fixture
    def manager(self):
        mgr = EmployeeManager()
        return mgr

    @pytest.mark.asyncio
    async def test_holding_sets_status(self, manager):
        """When agent returns __HOLDING:, task should be set to HOLDING."""
        task = AgentTask(id="t1", description="Send email to human")
        task.result = "__HOLDING:thread_id=abc123"
        # After _execute_task processes this result, task.status should be HOLDING
        # We test the parsing + status change logic directly
        meta = _parse_holding_metadata(task.result)
        assert meta is not None
        # The mechanism should set task.status = TaskPhase.HOLDING
        # and NOT set it to COMPLETE

    @pytest.mark.asyncio
    async def test_holding_persists_task(self, manager):
        """HOLDING task should be persisted to disk."""
        # Tested via integration in Task 5


class TestSetupReplyPoller:
    """Test reply poller cron setup."""

    @pytest.mark.asyncio
    async def test_setup_reply_poller_starts_cron(self):
        """_setup_reply_poller should call start_cron with correct params."""
        with patch("onemancompany.core.vessel.start_cron") as mock_cron:
            mock_cron.return_value = {"status": "ok"}
            mgr = EmployeeManager()
            mgr._setup_reply_poller("00010", "task123", "thread_abc", "1m")
            mock_cron.assert_called_once_with(
                "00010",
                "reply_task123",
                "1m",
                "[reply_poll] Check Gmail thread thread_abc for task task123",
            )

    @pytest.mark.asyncio
    async def test_setup_reply_poller_default_interval(self):
        """Default polling interval should be 1m."""
        with patch("onemancompany.core.vessel.start_cron") as mock_cron:
            mock_cron.return_value = {"status": "ok"}
            mgr = EmployeeManager()
            mgr._setup_reply_poller("00010", "task123", "thread_abc")
            call_args = mock_cron.call_args
            assert call_args[0][2] == "1m"  # interval argument
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel_holding.py -v`
Expected: FAIL — `_parse_holding_metadata` not yet defined

**Step 3: Implement _parse_holding_metadata**

Add to `vessel.py` (after the lazy-import wrappers, around line 102):

```python
def _parse_holding_metadata(result: str) -> dict | None:
    """Parse __HOLDING:key=value,... prefix from agent result.

    Returns dict of metadata if HOLDING prefix found, None otherwise.
    Only parses the first line.
    """
    if not result or not result.startswith("__HOLDING:"):
        return None
    first_line = result.split("\n", 1)[0]
    payload = first_line[len("__HOLDING:"):]
    if not payload.strip():
        return {}
    meta = {}
    for pair in payload.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            meta[k.strip()] = v.strip()
    return meta
```

**Step 4: Implement HOLDING detection in _execute_task**

Modify the "8. Mark completed" section (`vessel.py:881-888`). Replace:

```python
# 8. Mark completed
if task.status not in (TaskPhase.FAILED, TaskPhase.CANCELLED):
    task.status = TaskPhase.COMPLETE
    persist_task(employee_id, task)
```

With:

```python
# 8. Mark completed (or HOLDING)
if task.status not in (TaskPhase.FAILED, TaskPhase.CANCELLED):
    holding_meta = _parse_holding_metadata(task.result or "")
    if holding_meta is not None:
        task.status = TaskPhase.HOLDING
        persist_task(employee_id, task)
        # Start reply poller cron
        thread_id = holding_meta.get("thread_id", "")
        if thread_id:
            interval = holding_meta.get("interval", "1m")
            self._setup_reply_poller(employee_id, task.id, thread_id, interval)
        self._log(employee_id, task, "holding", f"Task entered HOLDING: {holding_meta}")
    else:
        task.status = TaskPhase.COMPLETE
        persist_task(employee_id, task)
```

**Step 5: Implement _setup_reply_poller method**

Add to `EmployeeManager` class (after `restore_persisted_tasks`):

```python
def _setup_reply_poller(
    self,
    employee_id: str,
    task_id: str,
    thread_id: str,
    interval: str = "1m",
) -> None:
    """Start a cron job to poll for email replies on a HOLDING task."""
    from onemancompany.core.automation import start_cron

    cron_name = f"reply_{task_id}"
    task_desc = f"[reply_poll] Check Gmail thread {thread_id} for task {task_id}"
    result = start_cron(employee_id, cron_name, interval, task_desc)
    if result.get("status") != "ok":
        logger.error("Failed to start reply poller for {}: {}", task_id, result)
```

**Step 6: Update post-HOLDING flow — skip history/callbacks for HOLDING tasks**

After the HOLDING detection, the existing code at lines 885-942 runs unconditionally. We need to guard:
- Lines 890-894: history + progress log — skip for HOLDING
- Lines 907-912: task tree callback — skip for HOLDING
- Lines 914-938: post-task cleanup — skip for HOLDING
- Lines 940-942: archive — skip for HOLDING (not terminal)

Wrap lines 885-942 with:

```python
if task.status != TaskPhase.HOLDING:
    if not task.completed_at:
        task.completed_at = datetime.now().isoformat()
    self._log(employee_id, task, "end", f"Task {task.status}")
    self._publish_task_update(employee_id, task)

    # 9. Record to history + progress log
    ... (existing code)

    # Post-task hook
    ... (existing code)

    # Task tree callback
    ... (existing code)

    # 10. Post-task cleanup
    ... (existing code)

    # 11. Archive task at terminal state
    ... (existing code)
else:
    self._publish_task_update(employee_id, task)
```

**Step 7: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel_holding.py -v`
Expected: PASS

**Step 8: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_vessel_holding.py
git commit -m "feat: add HOLDING mechanism — __HOLDING: prefix detection + reply poller cron"
```

---

### Task 3: resume_held_task Method

Add `resume_held_task()` to EmployeeManager and expose it as a base tool callable by agents.

**Files:**
- Modify: `src/onemancompany/core/vessel.py` (add `resume_held_task` method)
- Modify: `src/onemancompany/agents/common_tools.py` (add `resume_held_task` tool)
- Test: `tests/unit/core/test_vessel_holding.py` (add resume tests)

**Step 1: Write failing tests**

Add to `tests/unit/core/test_vessel_holding.py`:

```python
class TestResumeHeldTask:
    """Test resume_held_task transitions HOLDING → COMPLETE."""

    @pytest.fixture
    def manager_with_holding_task(self):
        mgr = EmployeeManager()
        # Create a task board with a HOLDING task
        from onemancompany.core.vessel import AgentTaskBoard
        board = AgentTaskBoard()
        task = AgentTask(id="held1", description="Waiting for human reply")
        task.status = TaskPhase.HOLDING
        task.result = "__HOLDING:thread_id=abc"
        board.tasks.append(task)
        mgr.boards["00010"] = board
        return mgr, task

    @pytest.mark.asyncio
    async def test_resume_sets_complete(self, manager_with_holding_task):
        mgr, task = manager_with_holding_task
        with patch("onemancompany.core.vessel.persist_task"):
            with patch("onemancompany.core.vessel.archive_task"):
                result = await mgr.resume_held_task("00010", "held1", "Human said: looks good!")
        assert result is True
        assert task.status == TaskPhase.COMPLETE
        assert task.result == "Human said: looks good!"

    @pytest.mark.asyncio
    async def test_resume_nonexistent_task(self, manager_with_holding_task):
        mgr, _ = manager_with_holding_task
        result = await mgr.resume_held_task("00010", "nonexistent", "reply")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_non_holding_task(self):
        mgr = EmployeeManager()
        from onemancompany.core.vessel import AgentTaskBoard
        board = AgentTaskBoard()
        task = AgentTask(id="t1", description="Normal task")
        task.status = TaskPhase.PROCESSING
        board.tasks.append(task)
        mgr.boards["00010"] = board
        result = await mgr.resume_held_task("00010", "t1", "reply")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_stops_poller_cron(self, manager_with_holding_task):
        mgr, task = manager_with_holding_task
        with patch("onemancompany.core.vessel.persist_task"):
            with patch("onemancompany.core.vessel.archive_task"):
                with patch("onemancompany.core.vessel.stop_cron") as mock_stop:
                    await mgr.resume_held_task("00010", "held1", "reply")
                    mock_stop.assert_called_once_with("00010", "reply_held1")
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel_holding.py::TestResumeHeldTask -v`
Expected: FAIL — `resume_held_task` not yet defined

**Step 3: Implement resume_held_task on EmployeeManager**

Add to `EmployeeManager` class:

```python
async def resume_held_task(
    self,
    employee_id: str,
    task_id: str,
    result: str,
) -> bool:
    """Resume a HOLDING task with the provided result.

    Transitions HOLDING → COMPLETE, stops the reply poller cron,
    persists the task, archives it, and triggers task tree callbacks.

    Returns True if task was found and resumed, False otherwise.
    """
    board = self.boards.get(employee_id)
    if not board:
        return False

    task: AgentTask | None = None
    for t in board.tasks:
        if t.id == task_id:
            task = t
            break
    if not task or task.status != TaskPhase.HOLDING:
        return False

    # Stop reply poller cron
    from onemancompany.core.automation import stop_cron
    stop_cron(employee_id, f"reply_{task_id}")

    # Transition to COMPLETE
    task.result = result
    task.status = TaskPhase.COMPLETE
    task.completed_at = datetime.now().isoformat()
    persist_task(employee_id, task)

    self._log(employee_id, task, "resumed", f"HOLDING → COMPLETE with result: {result[:200]}")
    self._publish_task_update(employee_id, task)

    # Record to history + progress log
    self._append_history(employee_id, task)
    summary = (task.result or "")[:200]
    _append_progress(employee_id, f"Completed (resumed): {task.description[:100]} → {summary}")

    # Task tree callback
    if task.project_dir:
        try:
            await self._on_child_complete(employee_id, task, project_id=task.project_id)
        except Exception as e:
            logger.error("Task tree callback failed for {}: {}", employee_id, e)

    # Archive
    if task.status in TERMINAL_STATES:
        archive_task(employee_id, task)

    return True
```

**Step 4: Add lazy-import wrapper for stop_cron in vessel.py module scope**

```python
def stop_cron(employee_id: str, cron_name: str) -> dict:
    """Lazy-import wrapper."""
    from onemancompany.core.automation import stop_cron as _stop
    return _stop(employee_id, cron_name)
```

**Step 5: Expose resume_held_task as a base tool**

Add to `common_tools.py` (near the other tools):

```python
@tool
def resume_held_task(task_id: str, result: str, employee_id: str = "") -> dict:
    """Resume a task that is in HOLDING state with the provided result.

    Use this when you have received a reply (e.g., from a human via email)
    for a task that is currently waiting (HOLDING).

    Args:
        task_id: The ID of the held task to resume.
        result: The result content to set on the task (e.g., email reply body).
        employee_id: Your employee ID.
    """
    import asyncio
    from onemancompany.core.agent_loop import get_agent_loop

    if not employee_id:
        return {"status": "error", "message": "employee_id required"}

    loop = get_agent_loop(employee_id)
    if not loop:
        return {"status": "error", "message": f"No agent loop for {employee_id}"}

    try:
        coro = loop.resume_held_task(employee_id, task_id, result)
        # Run the async method — we're inside a sync tool
        future = asyncio.ensure_future(coro)
        # The event loop is already running (we're inside LangChain), so use run_coroutine_threadsafe
        # Actually, since tools may be called from async context, try directly
        import concurrent.futures
        loop_obj = asyncio.get_event_loop()
        if loop_obj.is_running():
            # Schedule and wait
            task = asyncio.ensure_future(coro)
            # Can't block here — return immediately, rely on task completing
            return {"status": "ok", "message": f"Resume scheduled for task {task_id}"}
        else:
            ok = loop_obj.run_until_complete(coro)
            return {"status": "ok" if ok else "error", "message": "resumed" if ok else "task not found or not HOLDING"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
```

**Step 6: Register resume_held_task as a base tool**

In `_register_all_internal_tools()`, add `resume_held_task` to the `_base` list:

```python
_base = [
    list_colleagues, read, ls, write, edit, pull_meeting,
    report_to_ceo, ask_ceo, request_tool_access, load_skill,
    resume_held_task,
]
```

**Step 7: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel_holding.py -v`
Expected: PASS

**Step 8: Commit**

```bash
git add src/onemancompany/core/vessel.py src/onemancompany/agents/common_tools.py tests/unit/core/test_vessel_holding.py
git commit -m "feat: add resume_held_task — transitions HOLDING→COMPLETE + base tool"
```

---

### Task 4: HOLDING Task Restoration on Restart

When server restarts, HOLDING tasks should remain HOLDING (not reset to PENDING like PROCESSING tasks), and their reply poller crons should be re-started.

**Files:**
- Modify: `src/onemancompany/core/task_persistence.py` (keep HOLDING as-is in `load_active_tasks`)
- Modify: `src/onemancompany/core/vessel.py` (`restore_persisted_tasks` — restart pollers for HOLDING tasks)
- Test: `tests/unit/core/test_vessel_holding.py` (add restoration tests)

**Step 1: Write failing tests**

Add to `tests/unit/core/test_vessel_holding.py`:

```python
class TestHoldingRestoration:
    """Test that HOLDING tasks survive restart with cron re-setup."""

    def test_holding_not_reset_to_pending(self, tmp_path):
        """HOLDING tasks should stay HOLDING after load (unlike PROCESSING)."""
        from onemancompany.core.task_persistence import load_active_tasks, persist_task
        # This test verifies task_persistence.load_active_tasks keeps HOLDING status

    @pytest.mark.asyncio
    async def test_restore_restarts_pollers(self):
        """restore_persisted_tasks should restart reply pollers for HOLDING tasks."""
        mgr = EmployeeManager()
        from onemancompany.core.vessel import AgentTaskBoard
        board = AgentTaskBoard()
        task = AgentTask(id="held1", description="Waiting")
        task.status = TaskPhase.HOLDING
        task.result = "__HOLDING:thread_id=abc123"
        board.tasks.append(task)
        mgr.boards["00010"] = board

        with patch.object(mgr, "_setup_reply_poller") as mock_setup:
            # Simulate what restore_persisted_tasks should do for HOLDING tasks
            mgr._restart_holding_pollers()
            mock_setup.assert_called_once()
```

**Step 2: Verify task_persistence.py handles HOLDING correctly**

In `load_active_tasks()`, the current code resets PROCESSING→PENDING. Verify HOLDING is NOT reset. The existing code should be:

```python
if task.status == TaskPhase.PROCESSING:
    task.status = TaskPhase.PENDING
```

HOLDING is not mentioned, so it's preserved. Add a comment to make this explicit:

```python
# HOLDING tasks stay HOLDING — their reply pollers will be restarted
if task.status == TaskPhase.PROCESSING:
    task.status = TaskPhase.PENDING
```

**Step 3: Add _restart_holding_pollers to EmployeeManager**

Called at the end of `restore_persisted_tasks()`:

```python
def _restart_holding_pollers(self) -> int:
    """Restart reply poller crons for all HOLDING tasks."""
    count = 0
    for emp_id, board in self.boards.items():
        for task in board.tasks:
            if task.status == TaskPhase.HOLDING:
                meta = _parse_holding_metadata(task.result or "")
                if meta and meta.get("thread_id"):
                    interval = meta.get("interval", "1m")
                    self._setup_reply_poller(emp_id, task.id, meta["thread_id"], interval)
                    count += 1
    if count:
        logger.info("Restarted {} reply poller(s) for HOLDING tasks", count)
    return count
```

Call it at the end of `restore_persisted_tasks()`:

```python
def restore_persisted_tasks(self) -> int:
    ...
    if restored:
        logger.info("Restored {} task(s) from disk", restored)
    self._restart_holding_pollers()
    return restored
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel_holding.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/vessel.py src/onemancompany/core/task_persistence.py tests/unit/core/test_vessel_holding.py
git commit -m "feat: restore HOLDING tasks + restart reply pollers on server restart"
```

---

### Task 5: Integration Test — Full HOLDING Flow

End-to-end test of the complete flow: agent returns `__HOLDING:` → task enters HOLDING → cron starts → resume_held_task transitions to COMPLETE.

**Files:**
- Test: `tests/unit/core/test_vessel_holding.py` (add integration tests)

**Step 1: Write integration test**

```python
class TestHoldingIntegration:
    """Full flow: result → HOLDING → resume → COMPLETE."""

    @pytest.fixture
    def manager(self):
        mgr = EmployeeManager()
        return mgr

    @pytest.mark.asyncio
    async def test_full_holding_flow(self, manager):
        """Test: __HOLDING: result → HOLDING status → resume → COMPLETE."""
        # 1. Parse holding metadata
        result = "__HOLDING:thread_id=gmail_thread_123,interval=2m"
        meta = _parse_holding_metadata(result)
        assert meta == {"thread_id": "gmail_thread_123", "interval": "2m"}

        # 2. Simulate task with holding result
        task = AgentTask(id="flow1", description="Send task to human")
        task.result = result

        # 3. Detect holding
        assert meta is not None
        task.status = TaskPhase.HOLDING

        # 4. Setup poller (mock)
        from onemancompany.core.vessel import AgentTaskBoard
        board = AgentTaskBoard()
        board.tasks.append(task)
        manager.boards["00010"] = board

        with patch("onemancompany.core.vessel.start_cron") as mock_start:
            mock_start.return_value = {"status": "ok"}
            manager._setup_reply_poller("00010", "flow1", "gmail_thread_123", "2m")
            mock_start.assert_called_once()

        # 5. Resume
        with patch("onemancompany.core.vessel.persist_task"):
            with patch("onemancompany.core.vessel.archive_task"):
                with patch("onemancompany.core.vessel.stop_cron"):
                    ok = await manager.resume_held_task("00010", "flow1", "Human replied: All tests pass!")

        assert ok is True
        assert task.status == TaskPhase.COMPLETE
        assert task.result == "Human replied: All tests pass!"
        assert task.completed_at != ""
```

**Step 2: Run all tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel_holding.py -v`
Expected: ALL PASS

**Step 3: Run existing vessel tests to verify no regression**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel.py -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add tests/unit/core/test_vessel_holding.py
git commit -m "test: add integration tests for full HOLDING flow"
```

---

## Summary

| Task | What | Key Files |
|------|------|-----------|
| 1 | Talent files (profile, manifest, skills, tools) | `talent_market/talents/human_playtester/` |
| 2 | HOLDING mechanism (`__HOLDING:` detection + reply poller) | `vessel.py:881+` |
| 3 | `resume_held_task()` method + base tool | `vessel.py`, `common_tools.py` |
| 4 | HOLDING restoration on restart | `vessel.py`, `task_persistence.py` |
| 5 | Integration tests | `test_vessel_holding.py` |
