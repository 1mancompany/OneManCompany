# Task Timeout + Tree UI Design

## Goal

Two features: (1) robust task timeout with subprocess execution and OS-level cancel, (2) family-tree-style task tree visualization as the project detail view.

## Feature 1: Task Timeout + Subprocess Execution

### Executor Refactor

Deprecate `LangChainExecutor` (in-process). Company-hosted employees use a unified `SubprocessExecutor` that launches `launch.sh` as a child process.

`launch.sh` follows the ralph.sh pattern:
1. Start MCP server subprocess (`python -m onemancompany.tools.mcp.server`) for tool calls via stdin/stdout
2. Loop: call LLM (OpenRouter API) with system prompt + task + tool definitions
3. LLM returns tool call → execute via MCP stdio → return result to LLM
4. Detect completion signal → exit

`ClaudeSessionExecutor` remains for self-hosted employees only.

### SubprocessExecutor

Replaces `LangChainExecutor` and `ScriptExecutor`:
- `execute(task, context)`: starts `bash launch.sh` subprocess, passes task via env vars
- `cancel()`: two-stage kill protocol (see below)
- Captures stdout/stderr for result and logging

### Timeout

- `TaskNode` gains `timeout_seconds: int = 3600` (default 1 hour)
- `dispatch_child` gains optional `timeout_seconds` parameter
- `SubprocessExecutor.execute()` wraps with `asyncio.wait_for(..., timeout=timeout_seconds)`
- Only leaf nodes (no children) enforce timeout; parent nodes are driven by child lifecycle
- `TimeoutError` triggers cancel protocol → task marked FAILED with `"Timeout after {n}s"`
- `_on_child_complete()` fires normally, parent receives failed child notification

### Cancel Protocol

```
Timeout or CEO abort
    → process.terminate() (SIGTERM)
    → Poll every 5s: check if process exited
    → If exited within 30s → normal cleanup
    → If still alive after 30s → process.kill() (SIGKILL)
    → task.status = FAILED
    → _on_child_complete() notifies parent node
```

## Feature 2: Task Tree Visualization

### Backend: TaskTreeManager + FIFO Queue

`TaskTreeManager` class (one instance per project):
- Holds `TaskTree` reference + `asyncio.Queue` as FIFO change queue
- All tree modifications go through `queue.put(TreeEvent)`
- Single consumer coroutine processes serially: modify tree → save YAML → push WebSocket event
- Solves: concurrent writes (multiple employees finishing simultaneously) + drives real-time frontend updates

TreeEvent:
```python
@dataclass
class TreeEvent:
    type: str          # "node_added" | "node_updated" | "node_accepted" | "node_rejected" | "node_failed"
    node_id: str
    data: dict         # changed fields
    timestamp: str
```

API:
- `GET /api/projects/{project_id}/tree` — full tree structure (all nodes + hierarchy)
- WebSocket event `tree_update`: `{project_id, event_type, node_id, data}` — incremental push

### Frontend: Tree Rendering

Replaces project detail modal content. Two-panel layout:
- Left (70%): tree canvas
- Right (30%): node detail drawer (shown on click)

Tree rendering with D3.js tree layout, top-down:
- Drag to pan canvas
- Scroll to zoom
- Click node to show detail drawer

Node card (each tree node):
- Employee avatar (circular, pixel style)
- Employee name/nickname
- Task description (truncated, first 30 chars)
- Status badge: pending(gray) / processing(blue) / completed(yellow) / accepted(green) / failed(red)

Node detail drawer (right panel on click):
- Employee info: avatar + name + role
- Received prompt (full text, collapsible)
- Acceptance criteria (list)
- Child tasks / plan (if has children)
- Execution result (full text)
- Acceptance result (passed/failed + notes)
- Token usage + cost

Real-time updates: on `tree_update` WebSocket event, update the specific node's status/style without redrawing the full tree.
