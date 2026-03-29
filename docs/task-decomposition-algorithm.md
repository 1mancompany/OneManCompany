# Task Decomposition & Scheduling: Formal Algorithm Specification

> **Scope:** This document covers the **DAG execution layer** — the algorithms for decomposing, scheduling, and completing tasks within a single project iteration. For the organizational-level search that spans iterations and projects (strategy selection, employee assignment, convergence), see [organizational-evolution-mcts.md](organizational-evolution-mcts.md).
>
> In MCTS terms, this entire document describes what happens during a single **Simulation (rollout)**.

## 1. Theoretical Foundation

### 1.1 AND-OR Tree Decomposition

Task decomposition in OneManCompany is modeled as an **AND-tree** over a **DAG-augmented hierarchy**.

**Definition.** A task tree $T = (V, E_{\text{tree}}, E_{\text{dep}})$ where:
- $V$ is the set of task nodes
- $E_{\text{tree}} \subseteq V \times V$ is the parent-child tree edges (decomposition hierarchy)
- $E_{\text{dep}} \subseteq V \times V$ is the dependency edges (execution ordering constraints)

The combined graph $G = (V, E_{\text{tree}} \cup E_{\text{dep}})$ must be a **DAG** (Directed Acyclic Graph).

**AND-semantics.** A parent node $p$ is resolved iff **all** non-system children are resolved:

$$\text{resolved}(p) \iff \forall c \in \text{children}(p) \setminus S : \text{resolved}(c)$$

where $S$ is the set of system node types (REVIEW, CEO_REQUEST, WATCHDOG_NUDGE).

### 1.2 DAG Scheduling Theory

Scheduling follows a **topological-order-constrained FIFO** model:

**Definition.** A node $v$ is **ready** iff:

$$\text{ready}(v) \iff \text{status}(v) = \text{PENDING} \;\wedge\; \forall u \in \text{deps}(v) : \text{status}(u) \in \{\text{ACCEPTED}, \text{FINISHED}\}$$

The scheduler selects the first ready node in FIFO order per employee:

$$\text{next}(e) = \text{first}\{v \in Q_e \mid \text{ready}(v)\}$$

where $Q_e$ is employee $e$'s ordered schedule queue.

### 1.3 State Machine Formalization

The task lifecycle is a **finite state machine** $M = (\Sigma, \delta, s_0, F)$ where:
- $\Sigma = \{\text{PENDING}, \text{PROCESSING}, \text{HOLDING}, \text{COMPLETED}, \text{ACCEPTED}, \text{FINISHED}, \text{FAILED}, \text{BLOCKED}, \text{CANCELLED}\}$
- $s_0 = \text{PENDING}$
- $F = \{\text{FINISHED}, \text{CANCELLED}\}$ (terminal states)
- $\delta$ is the transition function:

$$\delta = \begin{cases}
\text{PENDING} &\to \{\text{PROCESSING}, \text{CANCELLED}\} \\
\text{PROCESSING} &\to \{\text{COMPLETED}, \text{HOLDING}, \text{FAILED}, \text{CANCELLED}\} \\
\text{HOLDING} &\to \{\text{PROCESSING}, \text{COMPLETED}, \text{FAILED}, \text{CANCELLED}\} \\
\text{COMPLETED} &\to \{\text{ACCEPTED}, \text{CANCELLED}\} \\
\text{ACCEPTED} &\to \{\text{FINISHED}\} \\
\text{FAILED} &\to \{\text{PROCESSING}\} \quad \text{(retry)} \\
\text{BLOCKED} &\to \{\text{PENDING}\} \quad \text{(unblock)}
\end{cases}$$

**Partition of states by semantic role:**

| Set | States | Meaning |
|-----|--------|---------|
| $\text{RESOLVED}$ | ACCEPTED, FINISHED, FAILED, CANCELLED | Decision made |
| $\text{UNBLOCKS}$ | ACCEPTED, FINISHED | Unblocks dependents |
| $\text{TERMINAL}$ | FINISHED, CANCELLED | No transitions out |
| $\text{WILL\_NOT\_DELIVER}$ | FAILED, BLOCKED, CANCELLED | No output |

---

## 2. Algorithm Design

### 2.1 Tree Construction with Cycle Detection

**Algorithm: `add_child`** (`task_tree.py:270`)

```
Input: parent_id, employee_id, description, depends_on[]
Output: new TaskNode or ValueError

1. parent ← tree.get_node(parent_id)
2. for each d in depends_on:
     if d ∉ tree.nodes → raise ValueError
3. if _has_cycle(depends_on) → raise ValueError
4. node ← TaskNode(parent_id, employee_id, description, depends_on)
5. parent.children_ids.append(node.id)
6. tree.nodes[node.id] ← node
7. return node
```

**Cycle Detection: `_has_cycle`** (`task_tree.py:312`)

Uses DFS on the dependency subgraph to detect if adding new edges creates a cycle:

$$\text{cycle}(E_{\text{dep}} \cup E_{\text{new}}) \iff \exists \text{ path } u \leadsto u \text{ in } (V, E_{\text{dep}} \cup E_{\text{new}})$$

```
Input: new_deps[] (proposed dependency edges)
Output: bool

1. for each start in new_deps:
     visited ← ∅
     stack ← [start]
     while stack ≠ ∅:
       current ← stack.pop()
       if current ∈ visited → continue
       visited ← visited ∪ {current}
       for each upstream in current.depends_on:
         if upstream = start ∧ current ≠ start → return True
         stack.push(upstream)
2. return False
```

**Complexity:** $O(|V| + |E_{\text{dep}}|)$ per call (standard DFS).

### 2.2 Dependency Resolution

**Algorithm: `all_deps_resolved`** (`task_tree.py:437`)

$$\text{all\_deps\_resolved}(v) = \begin{cases} \text{True} & \text{if } \text{deps}(v) = \emptyset \\ \bigwedge_{u \in \text{deps}(v)} \text{status}(u) \in \text{RESOLVED} & \text{otherwise} \end{cases}$$

**Algorithm: `_resolve_dependencies`** (`vessel.py:2521`)

Triggered when a node reaches a terminal/resolved state. Propagates through the dependency graph:

```
Input: tree, completed_node, project_dir
Effect: schedule/block/cancel dependent nodes

1. dependents ← tree.find_dependents(completed_node.id)
2. if dependents = ∅ → return
3. for each dep in dependents where dep.status = PENDING:
     a. if has_failed_deps(dep.id):
          if any dep has CANCELLED dependency → CASCADE CANCEL dep
          else → set dep.status ← BLOCKED, notify parent
     b. elif all_deps_resolved(dep.id):
          schedule_node(dep.employee_id, dep.id, tree_path)
          to_schedule ← to_schedule ∪ {dep.employee_id}
4. for each cascade_cancelled node → recurse _resolve_dependencies
5. for each emp_id in to_schedule → _schedule_next(emp_id)
6. DEADLOCK CHECK: if all non-root nodes ∈ {BLOCKED, FAILED, CANCELLED, ACCEPTED, FINISHED}
     → mark project FAILED
```

**Cascade cancellation** implements transitive closure:

$$\text{cancel}(v) \implies \forall w : v \in \text{deps}(w) \implies \text{cancel}(w)$$

### 2.3 Scheduling Algorithm

**Algorithm: `get_next_scheduled`** (`vessel.py:787`)

FIFO with dependency filtering — equivalent to a **constrained priority queue**:

$$\text{next}(e) = \arg\min_{i} \{Q_e[i] \mid \text{status}(Q_e[i]) = \text{PENDING} \wedge \text{all\_deps\_resolved}(Q_e[i])\}$$

```
Input: employee_id
Output: ScheduleEntry or None

1. for each entry in schedule[employee_id] (FIFO order):
     tree ← load_tree(entry.tree_path)
     node ← tree.get_node(entry.node_id)
     if node.status = PENDING ∧ tree.all_deps_resolved(node.id):
       return entry
2. return None
```

**Algorithm: `_schedule_next`** (`vessel.py:916`)

Non-blocking dispatcher with mutual exclusion (one task per employee):

```
Input: employee_id
Effect: start next task or set IDLE

1. if employee_id ∈ running_tasks → return  // mutual exclusion
2. entry ← get_next_scheduled(employee_id)
3. if entry = None → set_status(employee_id, IDLE); return
4. running_tasks[employee_id] ← create_task(_run_task(employee_id, entry))
```

**Invariant:** $|\text{running\_tasks}[e]| \leq 1 \quad \forall e$ (single-task-per-employee)

### 2.4 Bottom-Up Completion Propagation

**Algorithm: `_on_child_complete_inner`** (`vessel.py:2095`)

Implements a **bottom-up tree traversal** with validation gates:

```
Input: employee_id, entry (completed node), project_id

── Phase 0: Auto-Accept (runs before gates) ──
if node is SYSTEM_NODE_TYPE ∧ parent exists:
  siblings_completed ← {c ∈ children(parent) | c.type ∉ SYSTEM ∧ c.status = COMPLETED}
  if siblings_completed ≠ ∅ ∧ no active REVIEW:
    for each c in siblings_completed:
      c.status ← ACCEPTED → FINISHED
      trigger_dep_resolution(c)  // unblock downstream

── Gate 1: All Children Resolved ──
non_system_children ← {c ∈ children(parent) | c.type ∉ SYSTEM}
if ∀c ∈ non_system_children : status(c) ∈ {ACCEPTED, FINISHED}:
  parent.status ← COMPLETED → ACCEPTED → FINISHED
  RECURSE _on_child_complete_inner(parent)  // propagate upward

── Gate 2: Incremental Review ──
elif ∃c ∈ non_system_children : status(c) = COMPLETED:
  if no active REVIEW:
    _spawn_review_or_escalate(parent)  // create review node

── Gate 2a: Failed Child ──
elif ∃c ∈ non_system_children : status(c) = FAILED:
  resume parent with failure context (WATCHDOG_NUDGE)

── Gate 2b: Cancelled Child ──
elif ∃c ∈ children : status(c) = CANCELLED:
  resume parent with cancellation context

── Project Completion Check ──
if tree.is_project_complete():
  _request_ceo_confirmation(...)
```

**Correctness guarantee:** Auto-accept runs before Gate 2 to prevent the **review spawn loop** (a review finishing would otherwise trigger Gate 2 to spawn another review on still-COMPLETED siblings).

### 2.5 Review Circuit Breaker

**Algorithm: `_spawn_review_or_escalate`** (`vessel.py:2371`)

Implements bounded review with CEO escalation:

$$\text{review\_count}(p) = |\{c \in \text{children}(p) \mid c.\text{type} = \text{REVIEW} \wedge c.\text{employee} = p.\text{employee}\}|$$

```
if review_count(parent) ≥ MAX_REVIEW_ROUNDS:
  if no existing CEO_REQUEST for parent:
    create CEO_REQUEST node → escalate to CEO
    parent.status ← HOLDING
else:
  create REVIEW node → schedule for parent's employee
```

**Bound:** At most $k = \text{MAX\_REVIEW\_ROUNDS}$ (default 3) reviews per parent before CEO escalation. Guarantees termination.

### 2.6 Recovery Algorithm

**Algorithm: `recover_schedule_from_trees`** (`task_persistence.py:45`)

Restores scheduling state after server restart:

```
1. SCAN all task_tree.yaml files on disk
2. RESET: for each node where status = PROCESSING:
     node.status ← PENDING  // will be re-executed
3. AUTO-FINISH: for each COMPLETED node whose parent ∈ RESOLVED:
     node.status ← ACCEPTED → FINISHED  // orphan cleanup
4. SCHEDULE: for each PENDING node with all_deps_resolved:
     schedule_node(employee_id, node_id, tree_path)
5. HOLDING: schedule HOLDING nodes (watchdog handles timeout)
```

**Idempotency:** `schedule_node` is idempotent (dedup check), so recovery can run multiple times safely.

---

## 3. Implementation Reference

### 3.1 Core Functions

| Function | File | Line | Async | Algorithm |
|----------|------|------|-------|-----------|
| `TaskNode.set_status` | task_tree.py | 163 | N | FSM transition via `transition()` |
| `TaskTree.add_child` | task_tree.py | 270 | N | Tree construction + cycle detection |
| `TaskTree._has_cycle` | task_tree.py | 312 | N | DFS cycle detection on dep graph |
| `TaskTree.all_deps_resolved` | task_tree.py | 437 | N | $\bigwedge$ resolved check |
| `TaskTree.find_dependents` | task_tree.py | 433 | N | Reverse dep lookup |
| `TaskTree.has_failed_deps` | task_tree.py | 486 | N | $\bigvee$ failure check |
| `TaskTree.is_subtree_resolved` | task_tree.py | 449 | N | Recursive AND resolution |
| `transition` | task_lifecycle.py | 132 | N | FSM $\delta$ validation |
| `schedule_node` | vessel.py | 724 | N | Idempotent enqueue |
| `get_next_scheduled` | vessel.py | 787 | N | FIFO + dep filter |
| `_schedule_next` | vessel.py | 916 | N | Mutual-exclusion dispatch |
| `_on_child_complete_inner` | vessel.py | 2095 | Y | Bottom-up propagation |
| `_spawn_review_or_escalate` | vessel.py | 2371 | Y | Bounded review + escalation |
| `_resolve_dependencies` | vessel.py | 2521 | Y | DAG forward propagation |
| `_trigger_dep_resolution` | vessel.py | 323 | N | Sync→async bridge |
| `recover_schedule_from_trees` | task_persistence.py | 45 | N | Restart recovery |

### 3.2 Data Structures

| Structure | Type | Location | Purpose |
|-----------|------|----------|---------|
| `TaskNode` | dataclass | task_tree.py:40 | Node in decomposition tree |
| `TaskTree._nodes` | dict[str, TaskNode] | task_tree.py:250 | In-memory node index |
| `ScheduleEntry` | dataclass | vessel.py:87 | Pointer to scheduled node |
| `EmployeeManager._schedule` | dict[str, list[ScheduleEntry]] | vessel.py:710 | Per-employee FIFO queue |
| `EmployeeManager._running_tasks` | dict[str, asyncio.Task] | vessel.py:703 | Mutual exclusion guard |

### 3.3 Complexity Analysis

| Operation | Time | Space |
|-----------|------|-------|
| `add_child` (with cycle check) | $O(\|V\| + \|E_{\text{dep}}\|)$ | $O(\|V\|)$ |
| `all_deps_resolved` | $O(\|\text{deps}(v)\|)$ | $O(1)$ |
| `find_dependents` | $O(\|V\|)$ | $O(\|V\|)$ |
| `get_next_scheduled` | $O(\|Q_e\| \cdot \|\text{deps}\|)$ | $O(1)$ |
| `_resolve_dependencies` | $O(\|V\| + \|E_{\text{dep}}\|)$ | $O(\|V\|)$ |
| `is_subtree_resolved` | $O(\|V\|)$ | $O(\text{depth})$ |
| `recover_schedule_from_trees` | $O(\|P\| \cdot \|V\|)$ | $O(\|V\|)$ |

where $|P|$ = number of projects, $|V|$ = nodes per tree, $|Q_e|$ = queue length per employee.

---

## 4. Invariants & Guarantees

1. **DAG Invariant.** $G = (V, E_{\text{tree}} \cup E_{\text{dep}})$ is always acyclic. Enforced by `_has_cycle()` at insertion time.

2. **Single-Task Mutual Exclusion.** $\forall e : |\text{running}(e)| \leq 1$. Enforced by `_schedule_next` guard.

3. **Schedule Idempotency.** `schedule_node(e, n, p)` is idempotent: $\text{call}(e,n,p); \text{call}(e,n,p) \equiv \text{call}(e,n,p)$.

4. **Review Termination.** Review count bounded by $k$; escalation guarantees no infinite review loop.

5. **Cascade Completeness.** Cancellation of $v$ propagates to all transitive dependents: $\text{cancel}(v) \implies \text{cancel}(\text{closure}_{\text{dep}}(v))$.

6. **Dependency Resolution Completeness.** Every state transition to RESOLVED triggers `_trigger_dep_resolution`, ensuring no dependent is left permanently PENDING.

7. **Recovery Correctness.** After restart, PROCESSING nodes reset to PENDING (re-execution), and all PENDING nodes with resolved deps are re-scheduled.
