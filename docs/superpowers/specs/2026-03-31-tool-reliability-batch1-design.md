# Tool Reliability Batch 1 — System Prompt Guide + Dedicated Tools + Tool Template

## Problem

1. Agents don't know which tool to use for which task. No "use X for Y" guide in system prompt.
2. Common operations like updating work_principles require unreliable read→edit two-step via generic tools. LLM often skips or does it wrong.
3. No standard template for defining new tools — each tool is ad-hoc, varying in docstring quality, error format, and parameter style.

## Changes

### 1. Tool Selection Guide in System Prompt

Add a new section to `_get_tools_prompt_section()` in `base.py`, BEFORE the existing "Tool Usage Rules" section:

```
### Tool Selection Guide — Use the Right Tool

| Task | Tool | NOT this |
|------|------|----------|
| Update employee work principles | update_work_principles() | write/edit on .md file |
| Add CEO guidance note | update_guidance() | write/edit on guidance.yaml |
| Read/modify any file | read() then edit() or write() | bash cat/sed/echo |
| Search for files | glob_files() | bash find |
| Search file contents | grep_search() | bash grep/rg |
| Run shell commands | bash() | Only when no dedicated tool exists |
| Assign work to a colleague | dispatch_child() | Email/Gmail |
| Learn about colleagues | list_colleagues() | Reading profile files directly |
| Discuss with colleagues | pull_meeting() | dispatch_child with chat-like messages |
| View task details | read_node_detail() | Reading task_tree.yaml directly |

IMPORTANT:
- Always prefer dedicated tools over generic file operations.
- After modifying a file, verify the change: read() the file to confirm.
- If a tool returns an error, read the message — it tells you what to do next.
```

### 2. Dedicated Tools

#### `update_work_principles`

```python
@tool
async def update_work_principles(
    target_employee_id: str,
    content: str,
    employee_id: str = "",
) -> dict:
    """Update an employee's work principles.

    Replaces the entire work_principles.md file for the target employee.
    Use this instead of write()/edit() for work principles — it handles
    dirty-marking and ensures the frontend updates immediately.

    To add a new principle: read the current principles first (shown in your
    task context under "Your Work Principles"), then call this with the full
    updated content including the new principle.

    Args:
        target_employee_id: The employee whose principles to update (e.g. "00004").
        content: The complete new work principles content (Markdown).
        employee_id: Your own employee ID (auto-filled).
    """
```

Implementation: call `store.save_work_principles(target_employee_id, content)`. Returns `{"status": "ok", "employee_id": target_employee_id, "path": str(path)}`.

Category: **base** (all employees can update their own; managers can update others).

#### `update_guidance`

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
    that should persist as ongoing guidance.

    Args:
        target_employee_id: The employee to add guidance for (e.g. "00005").
        note: The guidance note text (one clear, actionable instruction).
        employee_id: Your own employee ID (auto-filled).
    """
```

Implementation: load existing notes via `store.load_employee_guidance`, append `note`, save via `store.save_guidance`. Returns `{"status": "ok", "employee_id": target_employee_id, "notes_count": len(notes)}`.

Category: **base**.

### 3. Tool Definition Template

Create `docs/tool-template.md` as the standard reference for all future tool definitions. This is a living document — every new tool MUST follow this template.

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

Success returns include relevant data fields:
    {"status": "ok", "path": "/path/to/file", "employee_id": "00004"}

Error returns include recovery hints:
    {"status": "error", "message": "Employee 99999 not found. Use list_colleagues() to find valid IDs."}

## Error Handling Rules

1. NEVER raise exceptions — always return {"status": "error", "message": ...}
2. Error messages MUST be actionable — tell the LLM what to do next
3. Include recovery hints for common mistakes:
   - Wrong employee_id → "Use list_colleagues() to find valid IDs"
   - File not found → "Use glob_files() to search, or ls() to browse"
   - Permission denied → "Use request_tool_access() to request permission"

## Registration

Register in _register_all_internal_tools() at the bottom of common_tools.py:

    tool_registry.register(my_tool, ToolMeta(name="my_tool", category="base"))

Categories:
- base: Available to all employees
- role: Restricted by employee role (set allowed_roles)
- asset: Loaded from company/assets/tools/

## Checklist for New Tools

- [ ] Docstring follows the 4-part standard (summary, when-to-use, args, constraints)
- [ ] Returns dict with "status" field
- [ ] Error messages include recovery hints
- [ ] Registered in tool_registry with correct category
- [ ] Added to docs/report/tool-inventory.md
- [ ] Added to system prompt tool selection guide (if user-facing)
- [ ] Unit test covers success + error + edge cases
```

### 4. Context Block Update

Remove the `wp_hint` line added in the closed PR #220. The dedicated `update_work_principles` tool replaces it — agents will see the tool in their tool list and use it directly.

Keep showing the file path in the context block (useful for `read()` if agent wants to inspect full content).

## What This Does NOT Change

- No changes to existing tool implementations (Batch 2)
- No input validation added (Batch 3)
- No error format changes to existing tools (Batch 2)
- No changes to tool_registry architecture

## Files Changed

| File | Change |
|------|--------|
| `src/onemancompany/agents/base.py` | Add tool selection guide to `_get_tools_prompt_section()` |
| `src/onemancompany/agents/common_tools.py` | Add `update_work_principles` + `update_guidance` tools, register them |
| `docs/tool-template.md` | New — standard tool definition template |
| `docs/report/tool-inventory.md` | Update with new tools |
| `tests/unit/agents/test_tool_reliability.py` | New — tests for both tools + prompt content |
