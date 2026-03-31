# Tool Reliability Batch 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add system prompt tool selection guide, two dedicated tools (update_work_principles, update_guidance), and a standard tool definition template.

**Architecture:** Two new @tool functions in common_tools.py calling store layer directly. Tool selection guide added to base.py prompt builder. Template doc created as docs/tool-template.md.

**Tech Stack:** Python 3.12, LangChain @tool decorator, pytest

**Venv:** `/Users/yuzhengxu/projects/OneManCompany/.venv/bin/python`

---

### Task 1: Add `update_work_principles` tool

**Files:**
- Modify: `src/onemancompany/agents/common_tools.py` (add tool + register)
- Test: `tests/unit/agents/test_tool_reliability.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/test_tool_reliability.py`:

```python
"""Tests for tool reliability Batch 1 — dedicated tools + prompt guide."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


class TestUpdateWorkPrinciples:
    @pytest.mark.asyncio
    async def test_updates_principles_and_returns_ok(self, tmp_path):
        from onemancompany.core.store import WORK_PRINCIPLES_FILENAME

        emp_dir = tmp_path / "00004"
        emp_dir.mkdir()
        (emp_dir / WORK_PRINCIPLES_FILENAME).write_text("old principles")

        with patch("onemancompany.agents.common_tools.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.agents.common_tools._store") as mock_store:
            mock_store.save_work_principles = AsyncMock()
            mock_store.load_employee = lambda eid: {"id": eid} if eid == "00004" else None

            from onemancompany.agents.common_tools import update_work_principles
            result = await update_work_principles.coroutine(
                target_employee_id="00004",
                content="# New Principles\n1. Be excellent",
                employee_id="00004",
            )
        assert result["status"] == "ok"
        assert result["employee_id"] == "00004"
        mock_store.save_work_principles.assert_awaited_once_with("00004", "# New Principles\n1. Be excellent")

    @pytest.mark.asyncio
    async def test_invalid_employee_returns_error(self):
        with patch("onemancompany.agents.common_tools._store") as mock_store:
            mock_store.load_employee = lambda eid: None

            from onemancompany.agents.common_tools import update_work_principles
            result = await update_work_principles.coroutine(
                target_employee_id="99999",
                content="anything",
                employee_id="00004",
            )
        assert result["status"] == "error"
        assert "99999" in result["message"]
        assert "list_colleagues" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_content_returns_error(self):
        from onemancompany.agents.common_tools import update_work_principles
        result = await update_work_principles.coroutine(
            target_employee_id="00004",
            content="",
            employee_id="00004",
        )
        assert result["status"] == "error"
        assert "empty" in result["message"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tool_reliability.py -v`
Expected: ImportError — `update_work_principles` does not exist.

- [ ] **Step 3: Implement the tool**

In `src/onemancompany/agents/common_tools.py`, add before `_register_all_internal_tools`:

```python
@tool
async def update_work_principles(
    target_employee_id: str,
    content: str,
    employee_id: str = "",
) -> dict:
    """Update an employee's work principles.

    Replaces the entire work_principles.md for the target employee.
    Use this instead of write()/edit() for work principles — it handles
    dirty-marking and ensures the frontend updates immediately.

    To add a new principle: read the current principles from your task context
    under "Your Work Principles", then call this with the full updated content
    including the new principle.

    Args:
        target_employee_id: The employee whose principles to update (e.g. "00004").
            Use list_colleagues() to find valid employee IDs.
        content: The complete new work principles content (Markdown).
        employee_id: Your own employee ID (auto-filled).
    """
    if not content or not content.strip():
        return {"status": "error", "message": "Content cannot be empty."}

    emp = _store.load_employee(target_employee_id)
    if not emp:
        return {
            "status": "error",
            "message": f"Employee '{target_employee_id}' not found. Use list_colleagues() to find valid IDs.",
        }

    await _store.save_work_principles(target_employee_id, content)
    from onemancompany.core.config import EMPLOYEES_DIR
    path = EMPLOYEES_DIR / target_employee_id / "work_principles.md"
    return {"status": "ok", "employee_id": target_employee_id, "path": str(path)}
```

Add a module-level lazy import alias at the top of common_tools.py (near other imports):

```python
from onemancompany.core import store as _store
```

Register in `_register_all_internal_tools`:

```python
    _base = [
        list_colleagues, read, ls, write, edit, pull_meeting,
        glob_files, grep_search,
        load_skill,
        resume_held_task, update_project_team,
        read_node_detail, view_meeting_minutes,
        bash, use_tool, set_project_budget,
        set_cron, stop_cron_job, setup_webhook, remove_webhook,
        list_automations,
        start_background_task, check_background_task, stop_background_task,
        list_background_tasks,
        update_work_principles, update_guidance,  # dedicated employee data tools
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tool_reliability.py::TestUpdateWorkPrinciples -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/agents/common_tools.py tests/unit/agents/test_tool_reliability.py
git commit -m "feat: add update_work_principles dedicated tool"
```

---

### Task 2: Add `update_guidance` tool

**Files:**
- Modify: `src/onemancompany/agents/common_tools.py` (add tool, already registered in Task 1)
- Test: `tests/unit/agents/test_tool_reliability.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/agents/test_tool_reliability.py`:

```python
class TestUpdateGuidance:
    @pytest.mark.asyncio
    async def test_appends_note_and_returns_ok(self):
        with patch("onemancompany.agents.common_tools._store") as mock_store:
            mock_store.load_employee = lambda eid: {"id": eid} if eid == "00005" else None
            mock_store.load_employee_guidance = lambda eid: ["Be proactive"]
            mock_store.save_guidance = AsyncMock()

            from onemancompany.agents.common_tools import update_guidance
            result = await update_guidance.coroutine(
                target_employee_id="00005",
                note="Focus on client communication",
                employee_id="00004",
            )
        assert result["status"] == "ok"
        assert result["notes_count"] == 2
        mock_store.save_guidance.assert_awaited_once_with("00005", ["Be proactive", "Focus on client communication"])

    @pytest.mark.asyncio
    async def test_invalid_employee_returns_error(self):
        with patch("onemancompany.agents.common_tools._store") as mock_store:
            mock_store.load_employee = lambda eid: None

            from onemancompany.agents.common_tools import update_guidance
            result = await update_guidance.coroutine(
                target_employee_id="99999",
                note="anything",
                employee_id="00004",
            )
        assert result["status"] == "error"
        assert "99999" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_note_returns_error(self):
        from onemancompany.agents.common_tools import update_guidance
        result = await update_guidance.coroutine(
            target_employee_id="00005",
            note="   ",
            employee_id="00004",
        )
        assert result["status"] == "error"
        assert "empty" in result["message"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tool_reliability.py::TestUpdateGuidance -v`
Expected: ImportError — `update_guidance` does not exist.

- [ ] **Step 3: Implement the tool**

In `src/onemancompany/agents/common_tools.py`, add after `update_work_principles`:

```python
@tool
async def update_guidance(
    target_employee_id: str,
    note: str,
    employee_id: str = "",
) -> dict:
    """Add a CEO guidance note to an employee's record.

    Appends a new guidance note. Does NOT replace existing notes.
    Use this after 1-on-1 meetings or when the CEO provides direction
    that should persist as ongoing guidance for the employee.

    Args:
        target_employee_id: The employee to add guidance for (e.g. "00005").
            Use list_colleagues() to find valid employee IDs.
        note: The guidance note text (one clear, actionable instruction).
        employee_id: Your own employee ID (auto-filled).
    """
    if not note or not note.strip():
        return {"status": "error", "message": "Note cannot be empty."}

    emp = _store.load_employee(target_employee_id)
    if not emp:
        return {
            "status": "error",
            "message": f"Employee '{target_employee_id}' not found. Use list_colleagues() to find valid IDs.",
        }

    existing = _store.load_employee_guidance(target_employee_id)
    existing.append(note.strip())
    await _store.save_guidance(target_employee_id, existing)
    return {"status": "ok", "employee_id": target_employee_id, "notes_count": len(existing)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tool_reliability.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/agents/common_tools.py tests/unit/agents/test_tool_reliability.py
git commit -m "feat: add update_guidance dedicated tool"
```

---

### Task 3: Add tool selection guide to system prompt

**Files:**
- Modify: `src/onemancompany/agents/base.py:509` (before "Tool Usage Rules" section)
- Test: `tests/unit/agents/test_tool_reliability.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/agents/test_tool_reliability.py`:

```python
class TestToolSelectionGuide:
    def test_prompt_contains_tool_selection_guide(self):
        from onemancompany.agents.base import get_employee_tools_prompt
        from unittest.mock import MagicMock
        from onemancompany.core.tool_registry import tool_registry

        with patch.object(tool_registry, "get_tools_for", return_value=[
            MagicMock(name="read", description="Read a file"),
            MagicMock(name="write", description="Write a file"),
        ]), patch.object(tool_registry, "get_meta", return_value=None):
            prompt = get_employee_tools_prompt("00010")

        assert "Tool Selection Guide" in prompt
        assert "update_work_principles" in prompt
        assert "update_guidance" in prompt
        assert "IMPORTANT" in prompt
        assert "verify the change" in prompt.lower()

    def test_prompt_contains_usage_rules(self):
        from onemancompany.agents.base import get_employee_tools_prompt
        from unittest.mock import MagicMock
        from onemancompany.core.tool_registry import tool_registry

        with patch.object(tool_registry, "get_tools_for", return_value=[
            MagicMock(name="bash", description="Run command"),
        ]), patch.object(tool_registry, "get_meta", return_value=None):
            prompt = get_employee_tools_prompt("00010")

        assert "Internal vs External" in prompt
        assert "dispatch_child" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tool_reliability.py::TestToolSelectionGuide -v`
Expected: FAIL — "Tool Selection Guide" not in prompt.

- [ ] **Step 3: Add the guide to `get_employee_tools_prompt`**

In `src/onemancompany/agents/base.py`, insert BEFORE line 509 (`parts.append("\n### Tool Usage Rules — Internal vs External")`):

```python
    parts.append(
        "\n### Tool Selection Guide — Use the Right Tool\n\n"
        "| Task | Tool | NOT this |\n"
        "|------|------|----------|\n"
        "| Update employee work principles | update_work_principles() | write/edit on .md file |\n"
        "| Add CEO guidance note | update_guidance() | write/edit on guidance.yaml |\n"
        "| Read/modify any file | read() then edit() or write() | bash cat/sed/echo |\n"
        "| Search for files | glob_files() | bash find |\n"
        "| Search file contents | grep_search() | bash grep/rg |\n"
        "| Run shell commands | bash() | Only when no dedicated tool exists |\n"
        "| Assign work to a colleague | dispatch_child() | Email/Gmail |\n"
        "| Learn about colleagues | list_colleagues() | Reading profile files directly |\n"
        "| Discuss with colleagues | pull_meeting() | dispatch_child with chat-like messages |\n"
        "| View task details | read_node_detail() | Reading task_tree.yaml directly |\n\n"
        "IMPORTANT:\n"
        "- Always prefer dedicated tools over generic file operations.\n"
        "- After modifying a file, verify the change: read() the file to confirm.\n"
        "- If a tool returns an error, read the message — it tells you what to do next."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tool_reliability.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/agents/base.py tests/unit/agents/test_tool_reliability.py
git commit -m "feat: add tool selection guide to agent system prompt"
```

---

### Task 4: Create tool definition template

**Files:**
- Create: `docs/tool-template.md`

- [ ] **Step 1: Create the template file**

Create `docs/tool-template.md`:

```markdown
# Tool Definition Template

Every LangChain tool in this codebase MUST follow this template.

## Docstring Standard

The docstring is the LLM's primary guide for using the tool. It must include:

1. **One-line summary** — What the tool does (imperative mood)
2. **When to use** — Explicit scenario guidance ("Use this when...", "Use this instead of...")
3. **Args block** — Every parameter with type, purpose, and example value
4. **Constraints** — What the tool does NOT do, limits, prerequisites

Example:

    """Update an employee's work principles.

    Use this instead of write()/edit() for work principles — it handles
    dirty-marking and ensures the frontend updates immediately.

    To add a new principle: read the current principles from your task context,
    then call this with the full updated content including the new principle.

    Args:
        target_employee_id: The employee ID (e.g. "00004"). Use list_colleagues()
            to find valid IDs.
        content: Complete new work principles in Markdown format.
        employee_id: Your own employee ID (auto-filled by the system).
    """

## Return Format

All tools MUST return a dict with:
- `"status"`: "ok" | "error"
- `"message"`: Human-readable description (on error, explains what went wrong)

Success example:

    {"status": "ok", "path": "/path/to/file", "employee_id": "00004"}

Error example:

    {"status": "error", "message": "Employee 99999 not found. Use list_colleagues() to find valid IDs."}

## Error Handling Rules

1. NEVER raise exceptions — always return `{"status": "error", "message": ...}`
2. Error messages MUST be actionable — tell the LLM what to do next
3. Include recovery hints for common mistakes:
   - Wrong employee_id: "Use list_colleagues() to find valid IDs"
   - File not found: "Use glob_files() to search, or ls() to browse"
   - Permission denied: "Use request_tool_access() to request permission"

## Registration

Register in `_register_all_internal_tools()` at the bottom of `common_tools.py`:

    tool_registry.register(my_tool, ToolMeta(name="my_tool", category="base"))

Categories:
- **base**: Available to all employees
- **role**: Restricted by employee role (set `allowed_roles`)
- **asset**: Loaded from `company/assets/tools/`

## Checklist for New Tools

- [ ] Docstring follows the 4-part standard (summary, when-to-use, args, constraints)
- [ ] Returns dict with "status" field
- [ ] Error messages include recovery hints
- [ ] Registered in tool_registry with correct category
- [ ] Added to `docs/report/tool-inventory.md`
- [ ] Added to system prompt tool selection guide (if user-facing)
- [ ] Unit test covers success + error + edge cases
```

- [ ] **Step 2: Update tool-inventory.md**

In `docs/report/tool-inventory.md`, update the "Planned New Tools" section to mark them as implemented:

Replace:
```markdown
## Planned New Tools (Batch 1)

| Tool | Category | Description | Rationale |
|------|----------|------------|-----------|
| `update_work_principles` | base | Update any employee's work principles | Generic write/edit unreliable for this; store.save_work_principles already exists |
| `update_guidance` | base | Add CEO guidance note to an employee | Same issue; store.save_guidance exists but not exposed as tool |
```

With:
```markdown
## Recently Added Tools (Batch 1)

| Tool | Category | Description | Quality |
|------|----------|------------|---------|
| `update_work_principles` | base | Update any employee's work principles (replaces write/edit) | Excellent |
| `update_guidance` | base | Add CEO guidance note (appends, does not replace) | Excellent |
```

- [ ] **Step 3: Commit**

```bash
git add docs/tool-template.md docs/report/tool-inventory.md
git commit -m "docs: add tool definition template and update inventory"
```

---

### Task 5: Full test suite + compilation verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -x -q`
Expected: All tests PASS, no regressions.

- [ ] **Step 2: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Verify new tools are registered**

Run: `.venv/bin/python -c "from onemancompany.core.tool_registry import tool_registry; names = tool_registry.all_tool_names(); assert 'update_work_principles' in names; assert 'update_guidance' in names; print(f'OK: {len(names)} tools registered')"`
Expected: `OK: 37 tools registered` (or similar count)

- [ ] **Step 4: Final commit if any fixups needed**

Only if previous steps reveal issues.
