# Task Tree Architecture Design

## Goal

Replace the current flat dispatch + subtask + acceptance state machine with a unified task tree. EA is the root node, child tasks are subtrees (sequential = deeper, parallel = same level), leaf nodes are executors. Results propagate upward, each parent decides whether to dispatch more children. When EA (root) is satisfied, it reports final results to CEO.

## Architecture

### Data Structure — TaskNode

```python
@dataclass
class TaskNode:
    id: str                              # uuid hex[:12]
    parent_id: str = ""                  # empty = root (EA)
    children_ids: list[str] = []

    employee_id: str = ""                # executor
    description: str = ""
    acceptance_criteria: list[str] = []  # set by parent on dispatch

    status: str = "pending"              # pending → processing → completed → accepted → failed
    result: str = ""
    acceptance_result: dict | None = None  # {passed: bool, notes: str}

    is_sequential: bool = False          # children execute as chain (A done → B starts)

    project_id: str = ""
    created_at: str = ""
    completed_at: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
```

### Tree Shape Example

```
EA (root)
├── COO: "开发登录功能"         ← parent dispatches with acceptance_criteria
│   ├── 工程师A: "写后端API"    ← parallel leaf
│   └── 工程师B: "写前端页面"   ← parallel leaf
│       └── 工程师B: "对接API"  ← sequential child of B (deeper subtree)
└── CSO: "准备发布文案"         ← parallel with COO
```

### Persistence

Each project gets `{project_dir}/task_tree.yaml` containing all nodes.

### LLM Trace

Each project gets `{project_dir}/llm_trace.jsonl` recording every LLM interaction:

```jsonl
{"node_id":"abc123","employee_id":"00003","ts":"...","type":"prompt","content":"..."}
{"node_id":"abc123","employee_id":"00003","ts":"...","type":"response","content":"...","model":"...","input_tokens":1200,"output_tokens":350}
{"node_id":"abc123","employee_id":"00003","ts":"...","type":"tool_call","content":{"tool":"accept_child","args":{...}}}
{"node_id":"abc123","employee_id":"00003","ts":"...","type":"tool_result","content":{"status":"ok"}}
```

## Task Lifecycle

### Creation

1. CEO gives task → system creates project + task_tree.yaml with EA as root
2. EA analyzes task, calls `dispatch_child(employee_id, description, acceptance_criteria)` to create children
3. Children's employees are awakened to execute

### Execution (Leaf Nodes)

Leaf nodes execute via existing Launcher protocol (LangChain / ClaudeSession / Script). Result written to `node.result`.

### Completion & Propagation

```
Leaf completes → code checks: all siblings done?
  ├── No → wait
  └── Yes → code checks: any failed children?
        ├── Yes → wake parent agent with all child results, parent decides: retry / reassign / mark failed & propagate up
        └── No → wake parent agent for acceptance review
              ├── accept_child() → node marked accepted, continue propagating up
              ├── reject_child(retry=True) → push correction task to same employee
              ├── reject_child(retry=False) → mark failed, parent decides next step
              └── dispatch_child() → add more children, wait for them to complete
```

### Sequential Children

Parent dispatches A. When A completes and is accepted, parent is woken, creates B as A's child (deeper level). B completes → propagates to A → propagates to parent.

### Reaching EA Root

EA reviews overall results. If satisfied and no more work needed → calls `report_to_ceo()` with final summary. Project marked complete.

## Parent Wake-Up Mechanism

When all parallel children complete, system pushes a review task to the parent employee:

```
你之前分发的子任务已全部完成，请审核结果：

子任务 1 (工程师A): 写后端API
  验收标准: [...]
  执行结果: "已完成REST API..."
  状态: completed

子任务 2 (工程师B): 写前端页面
  验收标准: [...]
  执行结果: "前端页面完成，但..."
  状态: failed — 原因: API对接失败

请对每个子任务调用 accept_child() 或 reject_child()。
如需追加任务，调用 dispatch_child()。
全部处理完毕后，你的任务将自动完成并向上汇报。
```

Code does basic checks first (all done? any failed?), then wakes parent agent for judgment.

## Tool Changes

### New Tools

- `dispatch_child(employee_id, description, acceptance_criteria)` — parent dispatches child task
- `accept_child(node_id, notes)` — parent accepts child result
- `reject_child(node_id, reason, retry)` — parent rejects child result

### Retained Tools

- `report_to_ceo()` — EA final report (existing)
- `set_project_budget()` — EA budget setting (existing)
- `list_colleagues()`, `read`, `ls`, `write`, `edit`, `pull_meeting` — execution tools (existing)

### Deleted Tools

- `dispatch_task()`, `dispatch_team_tasks()`, `create_subtask()`
- `set_acceptance_criteria()`, `accept_project()`, `ea_review_project()`
- `manage_tool_access()` tool access management (if no longer needed)

## Code Changes

### New Files

- `src/onemancompany/core/task_tree.py` — TaskNode dataclass + tree operations (load/save/query)
- `src/onemancompany/core/llm_trace.py` — LLM interaction recorder

### Major Modifications

- `vessel.py` — _execute_task driven by TaskNode, delete _post_task_cleanup state machine, add child-completion callback + parent wake-up
- `common_tools.py` — delete old dispatch/subtask/acceptance tools, add dispatch_child / accept_child / reject_child
- `ea_agent.py` — system prompt rewritten for task tree model
- `project_archive.py` — delete dispatch tracking functions

### Deleted Code

- `record_dispatch()`, `record_dispatch_completion()`, `record_dispatch_failure()`, `all_dispatches_complete()`, `get_ready_dispatches()`, `activate_dispatch()`
- `_post_task_cleanup` phase state machine (NEEDS_ACCEPTANCE, REJECTED, ACCEPTED, EA_APPROVED, EA_REJECTED)
- `_push_acceptance_task()`, `_push_ea_review_task()`, `_push_rectification_task()`
- All references to old tools in agent system prompts, tool registry, tests

### Unchanged

- Launcher protocol (LangChain / ClaudeSession / Script)
- EmployeeManager register/schedule mechanism
- Frontend WebSocket event-driven architecture
- Snapshot / hot-reload mechanism
- tool_registry unified tool system
