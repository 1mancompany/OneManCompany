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
