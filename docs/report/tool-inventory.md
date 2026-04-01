# Agent Tool Inventory

> Last updated: 2026-03-31

## Summary

- **Total tools**: 37
- **Quality**: Excellent 7, Good 17, Adequate 11, Poor 2
- **Categories**: base (27), role (8+), gated (0), asset (dynamic)

---

## File Operations (7)

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `read` | Read file content with offset/limit | `file_path`, `employee_id`, `offset`, `limit` | Excellent |
| `write` | Write/create file (must read before overwrite) | `file_path`, `content`, `employee_id`, `project_dir` | Good |
| `edit` | Exact string replacement in file | `file_path`, `old_string`, `new_string`, `replace_all`, `employee_id` | Good |
| `ls` | List directory contents | `dir_path`, `employee_id` | Good |
| `glob_files` | Find files by glob pattern (max 100 results) | `pattern`, `path`, `employee_id` | Adequate |
| `grep_search` | Regex search file contents (3 output modes) | `pattern`, `path`, `glob`, `case_insensitive`, `context_lines`, `output_mode`, `max_results`, `employee_id` | Good |
| `bash` | Execute shell command (120s default, 600s max) | `command`, `employee_id`, `timeout_seconds`, `description` | Good |

## Task Management (7)

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `dispatch_child` | Dispatch subtask to an employee | `employee_id`, `description`, `acceptance_criteria`, `title`, `timeout_seconds`, `depends_on`, `directive` | Excellent |
| `accept_child` | Accept a completed subtask | `node_id`, `notes` | Good |
| `reject_child` | Reject subtask (retry or fail) | `node_id`, `reason`, `retry` | Good |
| `cancel_child` | Cancel a subtask | `node_id`, `reason` | Adequate |
| `unblock_child` | Unblock a task with failed dependencies | `node_id`, `new_description` | Good |
| `resume_held_task` | Resume a HOLDING task with result | `task_id`, `result`, `employee_id` | Good |
| `read_node_detail` | Inspect full task node details | `node_id` | Adequate |

## Project Management (3)

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `create_project` | Create new project (standard/simple mode) | `task`, `mode` | Good |
| `set_project_name` | Set project display name | `name` | Adequate |
| `set_project_budget` | Set iteration budget before dispatching | `budget_usd` | Adequate |

## Team Collaboration (4)

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `list_colleagues` | List all colleagues with roles, skills, status | (none) | Good |
| `pull_meeting` | Initiate sync meeting (2+ participants, token-grab) | `topic`, `participant_ids`, `agenda`, `initiator_id` | Excellent |
| `update_project_team` | Add members to project team | `members` | Good |
| `view_meeting_minutes` | View meeting history for a room/project | `room_id`, `project_id`, `employee_id`, `limit` | Adequate |

## Automation (5)

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `set_cron` | Create/update recurring task (30s-1d intervals) | `cron_name`, `interval`, `task_description`, `employee_id` | Adequate |
| `stop_cron_job` | Stop a recurring task | `cron_name`, `employee_id` | **Poor** |
| `setup_webhook` | Create HTTP webhook trigger | `hook_name`, `task_template`, `employee_id` | Good |
| `remove_webhook` | Remove a webhook | `hook_name`, `employee_id` | **Poor** |
| `list_automations` | List all crons and webhooks | `employee_id` | Adequate |

## Background Tasks (4)

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `start_background_task` | Start long-running subprocess (max 5 concurrent) | `command`, `description`, `working_dir`, `employee_id` | Good |
| `check_background_task` | Check status and tail output | `task_id`, `tail`, `employee_id` | Good |
| `stop_background_task` | Stop subprocess (SIGTERM then SIGKILL) | `task_id`, `employee_id` | Adequate |
| `list_background_tasks` | List all background tasks with status | `employee_id` | Good |

## Other Common (3)

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `use_tool` | Use company equipment tool by name | `tool_name_or_id`, `employee_id` | Good |
| `request_tool_access` | Request access to a gated tool (COO approves) | `tool_name`, `reason`, `employee_id` | Good |
| `load_skill` | Load skill knowledge document | `skill_name` | Good |

## Role-Specific Tools

### HR (role: HR)

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `performance_review` | Conduct employee review (3.25/3.5/3.75 tiers, 3+ tasks required) | `employee_id`, `score`, `feedback` | Good |

### COO (role: COO)

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `manage_tool_access` | Grant/revoke tool permissions | `employee_id`, `tool_name`, `action`, `manager_id` | Good |
| `register_asset` | Register company asset tool (Python module) | (complex) | Excellent |
| `remove_tool` | Remove a registered tool | `tool_name` | Good |
| `list_assets` | List company assets | (none) | Adequate |
| `list_meeting_rooms` | List meeting rooms | (none) | Adequate |

### CSO (role: CSO)

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `list_sales_tasks` | List sales task queue | (none) | Adequate |
| `review_contract` | Approve/reject sales contract | `task_id`, `approved`, `notes` | Good |
| `complete_delivery` | Mark delivery complete | `task_id`, `delivery_summary` | Adequate |
| `settle_task` | Settle completed task (credit tokens) | `task_id` | Good |

---

## Employee Data Tools (2) — Added in Batch 1

| Tool | Description | Params | Quality |
|------|------------|--------|---------|
| `update_work_principles` | Update any employee's work_principles.md (replaces write/edit) | `target_employee_id`, `content`, `employee_id` | Excellent |
| `update_guidance` | Append CEO guidance note (does not replace existing) | `target_employee_id`, `note`, `employee_id` | Excellent |

---

## Quality Rating Criteria

- **Excellent**: Detailed docstring with examples, constraints, edge cases. LLM can act independently.
- **Good**: Clear purpose and params. Minor ambiguities but functional.
- **Adequate**: Minimal docstring. LLM can use it but may make mistakes.
- **Poor**: Missing or inadequate documentation. LLM likely to misuse.

## Known Gaps (from Claude Code comparison)

| Pattern | Claude Code | Our Status |
|---------|------------|------------|
| System prompt "use X for Y" guide | Explicit tool selection matrix | **Done (Batch 1)** — tool selection guide in prompt |
| Dedicated tools for common operations | Every frequent action has its own tool | **Done (Batch 1)** — update_work_principles, update_guidance |
| Structured error codes | `error_code` field in all errors | Missing — only `status` + `message` |
| Recovery hints in errors | `"hint": "try X instead"` | Missing |
| Input validation | Strict schema + validateInput() | Missing — no param validation |
| Verification prompts in results | `"next_step": "verify by ..."` | Missing |
