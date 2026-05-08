# Long Running Task Loop Optimization

## 背景

这次超过 10 小时的任务不是员工进程内部的代码死循环，而是任务编排层在失败、超时、通知、恢复之间反复创建系统节点。

典型现场：

- `653c66af49e6`: `index.html` 已经产出，但旧失败节点继续驱动 `[Child Task Failure]` 通知，后续产生大量 `watchdog_nudge` 节点。
- `e47e13cf01d8`: PDF 任务真实失败后，失败通知、timeout handler、转派节点继续堆积，最终形成失败处理风暴。

核心代码入口：

- `src/onemancompany/core/vessel.py:2488` `_on_child_complete_inner`
- `src/onemancompany/core/vessel.py:2636` failed child resume path
- `src/onemancompany/core/vessel.py:2672` creates `WATCHDOG_NUDGE` notification node
- `src/onemancompany/core/vessel.py:3145` `_full_cleanup`
- `src/onemancompany/core/task_tree.py:472` `is_project_complete`
- `src/onemancompany/core/task_persistence.py:45` schedule recovery
- `src/onemancompany/core/system_cron.py:513` holding timeout sweep

## Root Cause

### 1. Child failure notification is not idempotent

Current behavior in `_on_child_complete_inner`:

1. Any child completion callback checks whether the parent has any failed substantive child.
2. If yes, it builds a `[Child Task Failure]` prompt.
3. It always creates a new `WATCHDOG_NUDGE` child under the same parent.
4. That notification node itself completes, times out, or is retried, then the callback runs again.
5. The old failed child is still present, so another notification node is created.

This means the trigger condition is "parent has failed child", not "this failed child event has not been handled". The former is level-triggered and can fire forever; it should be edge-triggered and idempotent.

### 2. System notification nodes are treated too much like real work

Failure notification nodes use `NodeType.WATCHDOG_NUDGE`, but they still enter the normal execution path and default to `timeout_seconds=3600`. When many such nodes are created, they consume worker time and token budget even if the actual deliverable is already done.

### 3. Project completion does not quiesce old handlers

`_full_cleanup` marks the project complete and evicts the tree cache, but it does not explicitly cancel all pending/running notification, review, or stale handler nodes for that project. If stale scheduled entries survive in memory or are recovered from disk, they can continue after the deliverable is complete.

### 4. Recovery can revive stale work

`recover_schedule_from_trees` restores `PENDING` and `HOLDING` nodes from every non-archived tree. It does not currently check whether the project or iteration is already completed, failed, or cancelled before restoring those nodes.

## Recommended Fixes

## Implemented In This Patch

- Added `TaskNode.handled_child_failure_ids` and `TaskNode.event_key` for durable failure-notification dedupe.
- Changed child failure propagation to trigger only for the current failed substantive child, not for any old failed sibling.
- Failure notification nodes now use deterministic `child_failure:{parent_id}:{child_id}` keys and a shorter 180s timeout.
- Added `EmployeeManager.quiesce_project()` and call it from project cleanup to remove stale schedule entries and cancel active system handlers.
- Recovery now skips closed project/iteration trees with `archived`, `completed`, `failed`, or `cancelled` status.
- Added focused regression tests for serialization, duplicate failure notifications, watchdog completion, project quiescence, and closed-project recovery.

### P0: Make child failure handling idempotent

Add persistent handled-event metadata to `TaskNode`.

Recommended field:

```python
handled_child_failure_ids: list[str] = field(default_factory=list)
```

Then change the failed-child branch in `_on_child_complete_inner`:

- Only trigger when the current callback node is the failed substantive child.
- Skip if `node.id` is already in `parent_node.handled_child_failure_ids`.
- Append `node.id` before scheduling the notification node, while still inside the tree lock.
- Build the notification from the current failed node or currently unhandled failed nodes only.

Avoid adding a new lifecycle status like `superseded` as the first fix. The existing state machine is strict, and adding a new status would touch transitions, tests, UI color maps, recovery, and dependency logic. Prefer metadata plus `CANCELLED` for stale system nodes.

Expected shape:

```python
current_failed = (
    node.status == TaskPhase.FAILED.value
    and node.node_type not in SYSTEM_NODE_TYPES
)
already_handled = node.id in parent_node.handled_child_failure_ids

if current_failed and not already_handled and parent_node.status in (...):
    parent_node.handled_child_failure_ids.append(node.id)
    create_or_reuse_failure_notification(...)
```

Also add a deterministic notification key, for example:

```python
event_key = f"child_failure:{parent_node.id}:{node.id}"
```

Store it on the notification node using a new optional `event_key` field, or encode it in `title`. Before creating a node, search existing active system children with the same key and reuse them.

### P0: Add project quiescence on completion

Add a method on `EmployeeManager`, for example:

```python
def quiesce_project(self, project_id: str, tree_path: str, reason: str) -> int:
    ...
```

It should:

- Unschedule every entry whose node belongs to `project_id`.
- Cancel running tasks for that project when the node is a system handler or stale notification.
- Mark active `WATCHDOG_NUDGE`, `REVIEW`, and stale `CEO_REQUEST` nodes as `CANCELLED`.
- Stop `reply_*` and `holding_*` crons for cancelled nodes.
- Save the tree once after bulk mutation.

Call it from:

- `_full_cleanup` before `_release_project_resources`
- API archive/delete/abort paths where relevant
- Any direct project-complete path that bypasses `_full_cleanup`

For completed projects, do not keep failure handler nodes alive "for review"; preserve their content as audit history, but mark the active handler node cancelled with a reason such as `Superseded: project completed`.

### P0: Recovery must skip completed projects

In `recover_schedule_from_trees`, before restoring a project tree:

- Resolve the base project id from `tree.project_id`.
- Load project/iteration metadata.
- If status is `completed`, `failed`, `cancelled`, or archived, skip scheduling.
- Optionally run `quiesce_project` or a lighter cleanup pass for stale system nodes.

This prevents old `.onemancompany/.../task_tree.yaml` state from rearming notification loops after restart.

### P1: Bound system notification execution

Failure notification nodes should not default to one hour.

Recommended changes:

- Create failure notification nodes with `timeout_seconds=120` or `180`.
- Set a dedicated node type if needed, such as `FAILURE_NOTIFICATION`, or keep `WATCHDOG_NUDGE` but give it `event_key`.
- Disable retry for system notification nodes, or cap at one attempt.
- If a notification node completes without tool calls and only says the failure is stale, auto-finish it and do not reschedule the parent.

This makes handler bugs cheaper even if another edge case appears.

### P1: Make holding timeout notify the parent exactly once

`holding_timeout_sweep` currently auto-fails the held node and triggers dependency resolution. It should also route the timeout through the same idempotent child-complete path so the parent can react once.

After `_check_holding_timeout` returns true:

- Call `_on_child_complete` for the timed-out node.
- Rely on the handled failure id to prevent duplicate notifications.
- Unschedule the timed-out node after callback processing.

### P1: Add a loop breaker per parent

Even with idempotency, add a defensive cap:

- `MAX_FAILURE_NOTIFICATIONS_PER_PARENT`, for example 3.
- If exceeded, stop creating handler nodes and escalate once to CEO or mark the parent failed.
- Log a structured warning with `project_id`, `parent_id`, `failed_child_ids`, and existing notification ids.

This gives the system a hard upper bound.

### P2: Improve project-complete semantics around partial failures

`TaskTree.is_project_complete` treats failed child subtrees as resolved, which is useful for closure. But when deliverables exist and some speculative children failed, the finalizer should decide:

- Complete with warnings if required deliverables exist.
- Fail if required deliverables are missing.
- Cancel stale failure handlers either way.

The decision should happen once in project finalization, not repeatedly through notification nodes.

### P2: Add operational repair script

Create a one-time cleanup command for existing bad trees:

- Scan `company/business/projects/**/task_tree.yaml`.
- For projects already `completed`, `failed`, or `cancelled`, cancel active system nodes.
- For each parent, collapse duplicate `[Child Task Failure]` notification nodes:
  - keep the first as audit history,
  - mark the rest `CANCELLED`,
  - remove their schedule entries.
- Emit a summary: project id, cancelled nodes, duplicate groups, remaining active nodes.

This would directly clean cases like `653c66af49e6` and `e47e13cf01d8`.

## Test Plan

Add focused tests before implementing broad behavior:

- Repeated `_on_child_complete_inner` calls for the same failed child create only one notification.
- Completing a `WATCHDOG_NUDGE` does not re-trigger an old failed sibling.
- A second failed child under the same parent creates a second notification, not zero and not many.
- Project completion cancels pending/processing `WATCHDOG_NUDGE` nodes and unschedules them.
- `recover_schedule_from_trees` does not restore nodes for completed/failed/cancelled projects.
- `holding_timeout_sweep` notifies the parent once and does not create duplicate notifications on the next sweep.
- Existing `dispatch_child`, `accept_child`, and `reject_child` tests still pass.

## Implementation Order

1. Add `TaskNode.handled_child_failure_ids` plus serialization tests.
2. Refactor the failed-child branch in `_on_child_complete_inner` to be current-event based and idempotent.
3. Add `event_key` or equivalent dedupe for system notification nodes.
4. Add `quiesce_project` and call it from `_full_cleanup`.
5. Make recovery skip closed projects.
6. Tighten system notification timeout/retry behavior.
7. Add the repair script for existing project trees.

## Expected Outcome

After these changes:

- A failed child can wake its parent once.
- A completed notification cannot keep reprocessing the same old failure.
- A completed project becomes quiet: no pending failure handlers, no stale crons, no recovered stale schedule entries.
- Timeout/failure storms become bounded, observable, and cheap instead of open-ended.
