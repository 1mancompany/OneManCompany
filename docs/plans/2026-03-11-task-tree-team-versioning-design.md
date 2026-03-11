# Task Tree Branching, Team Workflow, and Version Management — Design

**Goal:** Fix task tree overwriting on CEO follow-up, enforce EA→O-level dispatch hierarchy, upgrade COO project workflow with team assembly and meetings, add project team tracking with bidirectional frontend display, and set up semantic versioning with automated releases.

## 1. Task Tree Branch Model

### Problem

When CEO adds follow-up instructions via `task_followup`, a new child is appended under the existing root. But the root's status may already be completed/accepted, and old children mix with new ones — `all_children_terminal` checks in vessel break.

### Design

Introduce `branch` concept to TaskNode and TaskTree:

**TaskNode additions:**
- `branch: int = 0` — which branch this node belongs to
- `branch_active: bool = True` — whether this node is on the current active branch

**TaskTree additions:**
- `current_branch: int = 0` — current active branch number
- `new_branch() -> int` — increments `current_branch`, marks all existing nodes as `branch_active=False`, returns new branch number

**`task_followup` changes:**
1. Load existing tree
2. Call `tree.new_branch()` — deactivates old branch
3. Create new child under root with `branch=current_branch, branch_active=True`
4. Root node itself gets updated to `branch=current_branch, branch_active=True` (root spans all branches)
5. Save tree

**vessel `_on_task_done` changes:**
- `all_children_terminal` check filters to `branch_active=True` children only
- Review prompt only includes `branch_active=True` children

**Frontend task tree rendering:**
- `branch_active=False` nodes: dashed border, dimmed color
- `branch_active=True` nodes: solid border, thicker line, full color
- Branch number label on each branch group

## 2. EA Dispatch Constraint

### Problem

EA tends to dispatch tasks directly to engineers, bypassing COO/HR/CSO.

### Design

**Prompt change** (`ea_agent.py`):

Replace the routing table with strict O-level-only dispatch:

```
任务路由 (严格执行):
- 人事/招聘/入职 → dispatch_child("00002", ...) HR
- 项目执行/开发/设计/运营 → dispatch_child("00003", ...) COO
- 销售/市场/客户 → dispatch_child("00005", ...) CSO
- 绝对禁止直接 dispatch_child 给普通员工 (00006+)
```

**Code enforcement** (`tree_tools.py` `dispatch_child`):

Add caller validation: if the calling employee is EA (00004), target must be in `{HR_ID, COO_ID, CSO_ID}`. Otherwise return error message guiding EA to dispatch to the correct O-level.

## 3. COO Project Workflow Upgrade

### Problem

COO dispatches tasks directly without team assembly or alignment meetings.

### Design

**Prompt change** (`coo_agent.py`):

Add four-phase project execution protocol:

```
项目执行流程 (复杂项目必须遵循，简单任务可跳过阶段2-3):

阶段1 — 分析项目:
  理解CEO/EA的需求，评估复杂度和所需技能
  决定是否需要组建团队（简单单人任务可直接dispatch）

阶段2 — 组建团队:
  list_colleagues() 查看可用人员及其技能和当前负载
  update_project_team(project_id, members) 注册团队成员
  可在后续阶段追加成员

阶段3 — 团队对齐:
  pull_meeting(团队全员) 讨论:
    - 项目目标和范围
    - 验收标准
    - 分工计划和时间线
  会议结论写入项目工作区

阶段4 — 分派执行:
  按计划 dispatch_child() 分配子任务
  每个子任务必须有明确的验收标准（来自阶段3讨论结果）
```

**New tool**: `update_project_team` — COO-only tool that writes team members to project.yaml.

```python
@tool
def update_project_team(project_id: str, members: list[dict]) -> str:
    """Update the team roster for a project.

    Args:
        project_id: The project ID.
        members: List of {employee_id, role} dicts to add to the team.

    Returns:
        Confirmation message.
    """
```

Tool writes to project.yaml `team` field. Appends (does not overwrite) — existing members preserved.

## 4. Project Team Data Model + Frontend

### Data Model

`project.yaml` new field:

```yaml
team:
  - employee_id: "00003"
    role: "项目负责人"
    joined_at: "2026-03-11T10:00:00"
  - employee_id: "00006"
    role: "Game Engineer"
    joined_at: "2026-03-11T10:05:00"
```

### Frontend — Project Detail

Project modal adds "TEAM" section:

- List of team members with: employee avatar/emoji + name + role + join date
- Clicking a member opens their employee detail

### Frontend — Employee Detail

Employee modal adds "PROJECT HISTORY" section:

- Reverse lookup: scan all project.yaml files where `team[].employee_id` matches
- Display: project name + role in project + status (in_progress/completed)
- Clicking a project opens its detail

### API

`GET /api/project/{project_id}/detail` already returns project data — ensure `team` field is included.

New endpoint or extend existing: `GET /api/employees/{employee_id}/projects` — returns list of projects this employee participated in.

## 5. Version Management

### Setup

- **Versioning**: Semantic versioning (semver) in `pyproject.toml`
- **Tool**: `python-semantic-release`
- **Commit format**: Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `BREAKING CHANGE:`)
- **Changelog**: Auto-generated `CHANGELOG.md`
- **Release**: `semantic-release publish` → bumps version, generates changelog, creates git tag, creates GitHub Release

### Changes

1. Add `python-semantic-release` to dev dependencies
2. Configure `[tool.semantic_release]` in `pyproject.toml`:
   - `version_toml = ["pyproject.toml:project.version"]`
   - `branch = "main"`
   - `changelog_file = "CHANGELOG.md"`
   - `build_command = ""`  (no build step needed)
3. Remove `.git/hooks/post-commit` (conflicts with conventional commits)
4. Remove README changelog markers (`<!-- CHANGELOG_START -->` / `<!-- CHANGELOG_END -->`)
5. Set initial version to `0.1.0` in `pyproject.toml`

### Workflow

```
Developer commits with conventional format →
semantic-release version (bumps version + changelog + tag) →
semantic-release publish (GitHub Release)
```

## 6. Files to Modify

| File | Change |
|------|--------|
| `src/onemancompany/core/task_tree.py` | Add `branch`, `branch_active` to TaskNode; `current_branch`, `new_branch()` to TaskTree |
| `src/onemancompany/core/vessel.py` | Filter `branch_active=True` in `_on_task_done` children checks |
| `src/onemancompany/api/routes.py` | Update `task_followup` to use `new_branch()`; add employee projects endpoint |
| `src/onemancompany/agents/ea_agent.py` | Strict O-level-only dispatch prompt |
| `src/onemancompany/agents/tree_tools.py` | EA caller validation in `dispatch_child` |
| `src/onemancompany/agents/coo_agent.py` | Four-phase project workflow prompt |
| `src/onemancompany/agents/common_tools.py` or new file | `update_project_team` tool |
| `src/onemancompany/core/project_archive.py` | Support `team` field in project data |
| `frontend/app.js` | Project team section, employee project history, task tree branch rendering |
| `frontend/index.html` | HTML for new sections |
| `frontend/style.css` | Styles for team section, project history, branch rendering |
| `pyproject.toml` | Add version, semantic-release config |
| `.git/hooks/post-commit` | Remove |
| `CHANGELOG.md` | Create (auto-generated) |
