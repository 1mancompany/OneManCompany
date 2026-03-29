# Organizational Evolution via MCTS: Theoretical Foundation

## Abstract

We model the evolution of an AI-driven organization as a **Monte Carlo Tree Search (MCTS)** process operating over a hierarchical task decomposition space. Each organizational decision — task decomposition, personnel assignment, review, and iteration — maps to a phase of MCTS. This framework provides theoretical guarantees on convergence, explains the exploration-exploitation trade-off in organizational learning, and grounds the system's design in established decision-theoretic principles.

---

## 1. MCTS Primer

Monte Carlo Tree Search is a best-first search algorithm that builds a decision tree incrementally through four phases:

1. **Selection** — traverse the tree from root using a tree policy (e.g., UCB1) to find the most promising unexplored node
2. **Expansion** — add one or more child nodes representing new actions
3. **Simulation** — perform a rollout (playout) from the new node to estimate its value
4. **Backpropagation** — update value estimates along the path from the new node back to the root

The key insight: MCTS does not require a complete model of the environment. It learns the value of actions through **repeated simulation and statistical aggregation**.

---

## 2. Mapping: Organization as MCTS

### 2.1 State Space

$$\mathcal{S} = \{s \mid s = (\text{project\_state}, \text{team\_state}, \text{resource\_state})\}$$

A state $s$ encodes:
- **Project state**: task tree structure, node statuses, dependencies, results
- **Team state**: employee skills, workload, performance history, availability
- **Resource state**: token budget, cost accumulation, time constraints

### 2.2 Action Space

$$\mathcal{A} = \mathcal{A}_{\text{decompose}} \cup \mathcal{A}_{\text{assign}} \cup \mathcal{A}_{\text{review}} \cup \mathcal{A}_{\text{iterate}}$$

| Action Type | Description | Implementation |
|-------------|-------------|----------------|
| $a_{\text{decompose}}$ | Break task into subtasks | `dispatch_child()` in tree_tools.py |
| $a_{\text{assign}}$ | Assign subtask to employee | `employee_id` field in `dispatch_child()` |
| $a_{\text{review}}$ | Accept or reject result | `accept_child()` / `reject_child()` |
| $a_{\text{iterate}}$ | Start new iteration with updated strategy | `create_iteration()` in project_archive.py |
| $a_{\text{escalate}}$ | Escalate decision to CEO | `_spawn_review_or_escalate()` circuit breaker |

### 2.3 The Four Phases

#### Phase 1: Selection — Strategic Decomposition

**MCTS**: Traverse tree using UCB1 to select most promising node.

**Organization**: EA analyzes CEO's task and selects a decomposition strategy. COO assigns employees based on skills and availability.

$$\text{UCB1}(v) = \bar{X}_v + C \sqrt{\frac{\ln N(\text{parent}(v))}{N(v)}}$$

**Organizational analogue:**

$$\text{AssignScore}(e, t) = \underbrace{Q(e, t)}_{\text{exploitation}} + C \cdot \underbrace{\sqrt{\frac{\ln \sum_j N_j}{N_e}}}_{\text{exploration}}$$

where:
- $Q(e, t)$ = expected quality of employee $e$ on task type $t$ (from `performance_history`, `task_histories`)
- $N_e$ = number of tasks previously assigned to employee $e$
- $C$ = exploration constant balancing trying new employees vs. using proven ones

**Implementation mapping:**
- EA's system prompt includes employee capabilities → **selection policy**
- COO's `dispatch_child(employee_id=...)` → **action selection**
- `list_colleagues()` tool → **state observation for selection**

#### Phase 2: Expansion — Task Tree Growth

**MCTS**: Add new child nodes to the selected node.

**Organization**: `dispatch_child()` creates new TaskNode children, expanding the decomposition tree.

$$T_{t+1} = T_t \cup \{v_{\text{new}}\} \quad \text{where } v_{\text{new}} = \text{dispatch\_child}(v_{\text{parent}}, e, d, \text{deps})$$

**Key properties:**
- **DAG constraint**: `_has_cycle()` ensures $G = (V, E_{\text{tree}} \cup E_{\text{dep}})$ remains acyclic
- **Branching factor**: unbounded (LLM decides decomposition granularity)
- **Dependency edges**: $E_{\text{dep}}$ encodes execution ordering, analogous to MCTS's action prerequisites

**Implementation:**
- `TaskTree.add_child()` → node creation with cycle validation
- `depends_on` field → partial ordering constraints on expansion

#### Phase 3: Simulation — Employee Execution (Rollout)

**MCTS**: Perform random/heuristic playout from new node to estimate value.

**Organization**: Employee executes the task using LLM inference. The execution IS the simulation — the employee "plays out" the task to produce a result.

$$\text{rollout}(v) = \text{execute}(v.\text{employee}, v.\text{description}) \to (r, c, q)$$

where:
- $r$ = result text (task output)
- $c$ = cost in USD (`cost_usd` from token usage)
- $q$ = quality signal (acceptance/rejection by reviewer)

**Non-random rollout**: Unlike classical MCTS which uses random playouts, our "simulation" uses a **learned policy** (the LLM). This is analogous to AlphaGo's neural network rollout replacing random playouts — dramatically improving simulation quality.

**Implementation:**
- `_execute_task()` → runs LangChain/Claude/OpenClaw executor
- `LaunchResult` → captures output, tokens, cost
- `execution.log` (JSONL) → full execution trace for analysis

#### Phase 4: Backpropagation — Review & Value Update

**MCTS**: Update value estimates from leaf to root.

**Organization**: Review results propagate upward. Accept/reject decisions update the tree. Cost and quality metrics flow back to inform future decisions.

$$V(v) \leftarrow V(v) + \Delta(v) \quad \forall v \in \text{path}(\text{leaf}, \text{root})$$

**Organizational backpropagation signals:**

| Signal | Direction | Mechanism | Implementation |
|--------|-----------|-----------|----------------|
| Quality | leaf → root | accept_child / reject_child | `_on_child_complete_inner()` Gate 1/Gate 2 |
| Cost | leaf → root | Token usage aggregation | `record_project_cost()` |
| Failure | leaf → root | FAILED status + retry/escalate | Gate 2a: failed child resumption |
| Blocking | sideways | Dependency resolution | `_resolve_dependencies()` |
| Learning | cross-project | Performance history | `_append_history_from_node()`, `progress.log` |

**Recursive propagation** (Gate 1):
$$\text{resolved}(p) \iff \forall c \in \text{children}(p) \setminus S : c.\text{status} \in \{\text{ACCEPTED}, \text{FINISHED}\}$$

When all children resolve → parent auto-promotes → recursively propagates to grandparent → ... → CEO root.

---

## 3. Organizational Evolution as Iterated MCTS

### 3.1 Iteration = Re-Planning with Updated Beliefs

Each project iteration (iter_001, iter_002, ...) corresponds to a **full MCTS episode**:

$$\pi_{k+1} = \text{MCTS}(s_k, \pi_k, \text{history}_k)$$

where:
- $s_k$ = state after iteration $k$ (including partial results)
- $\pi_k$ = policy learned from iterations $1..k$
- $\text{history}_k$ = accumulated execution traces, costs, quality signals

**CEO follow-up** ("完成了吗？", "加一个...") = injecting new information into the search, analogous to MCTS receiving updated reward signals and re-planning.

**Implementation:**
- `create_iteration()` → new MCTS episode
- CEO follow-up tasks → root node for new iteration
- Previous iteration results accessible via `project_history_context()`

### 3.2 Multi-Armed Bandit at Assignment Level

Employee assignment is a **contextual multi-armed bandit** problem embedded within MCTS:

$$e^* = \arg\max_{e \in \text{employees}} \left[ \hat{\mu}_e(t) + \alpha \cdot \hat{\sigma}_e(t) \right]$$

where:
- $\hat{\mu}_e(t)$ = estimated quality of employee $e$ on task type $t$
- $\hat{\sigma}_e(t)$ = uncertainty in that estimate
- $\alpha$ = exploration parameter

**Current implementation** (implicit): EA/COO's LLM acts as the bandit policy, using employee skills and history in the prompt context. The policy is **not explicitly parameterized** but emerges from the LLM's reasoning over:
- `list_colleagues()` output (employee capabilities)
- `progress.log` (past task outcomes)
- `work_principles.md` (accumulated guidance)

### 3.3 Branch-and-Bound Pruning

The `branch` + `branch_active` mechanism implements **branch-and-bound**:

$$\text{prune}(v) \iff \text{LB}(v) > \text{UB}(\text{best\_found})$$

In organizational terms:
- **Branch creation**: COO/EA explores alternative decomposition strategies
- **Branch deactivation** (`branch_active = False`): abandon a strategy that's not working
- **Active branch**: the currently pursued strategy

This is equivalent to MCTS's **progressive widening** — gradually expanding the branching factor as more simulations reveal which branches are promising.

---

## 4. Convergence Properties

### 4.1 MCTS Convergence Theorem (Kocsis & Szepesvari, 2006)

**Theorem.** With UCB1 tree policy, MCTS converges to the optimal action as the number of simulations approaches infinity:

$$\lim_{N \to \infty} P(\hat{a}^* = a^*) = 1$$

### 4.2 Organizational Convergence

In our system, "convergence" means the organization improves at task execution over time:

$$\lim_{k \to \infty} \mathbb{E}[\text{quality}_k] \to q^* \quad \text{and} \quad \lim_{k \to \infty} \mathbb{E}[\text{cost}_k] \to c^*$$

**Mechanisms driving convergence:**

| Mechanism | MCTS Analogue | Implementation |
|-----------|---------------|----------------|
| Employee skill accumulation | Improved rollout policy | `work_principles.md` updates via guidance |
| Task history compression | Value function approximation | `_maybe_compress_history()` |
| Retry with rejection feedback | Regret minimization | `reject_child(retry=True)` |
| Cross-project learning | Transfer learning across episodes | `progress.log` injected into prompts |
| CEO guidance refinement | Human-in-the-loop reward shaping | Inbox accept/reject/follow-up |

### 4.3 Bounded Rationality & Circuit Breakers

Real organizations cannot run infinite simulations. Our system implements **bounded rationality** through:

1. **Review round limit** ($k = 3$): prevents infinite review loops
   $$\text{review\_count}(v) \geq k \implies \text{escalate\_to\_CEO}(v)$$

2. **Task timeout** ($T = 3600s$): bounds single-rollout duration
   $$t_{\text{exec}}(v) > T \implies \text{status}(v) \leftarrow \text{FAILED}$$

3. **Cost budget** (planned): bounds total simulation cost
   $$\sum_{v \in T} c(v) > B \implies \text{pause}$$

These correspond to MCTS's **computational budget** — stop searching and commit to the best-known action.

---

## 5. Comparison: MCTS vs. Our System

| MCTS Concept | Organizational Analogue | Key Difference |
|---|---|---|
| Tree node | TaskNode | Nodes contain rich semantic state (descriptions, results) |
| Random rollout | Employee execution | **Learned policy** (LLM) instead of random |
| UCB1 selection | EA/COO task routing | **Implicit** via LLM reasoning, not explicit formula |
| Backpropagation | Review + cost aggregation | **Multi-signal** (quality, cost, time) not scalar |
| Computational budget | Review limit + timeout | **Human-in-the-loop** (CEO) as ultimate arbiter |
| Progressive widening | Branch mechanism | **Semantic** branching (alternative strategies) |
| Transposition table | Task dedup (`schedule_node`) | Prevents re-exploring same state |
| Terminal node | FINISHED / CANCELLED status | **State machine** with multiple terminal states |

---

## 6. Future Directions

### 6.1 Explicit UCB1 for Employee Assignment

Replace implicit LLM-based assignment with explicit bandit:

$$e^* = \arg\max_{e} \left[ \frac{\text{accepted}(e, t)}{\text{total}(e, t)} + C \sqrt{\frac{\ln \sum_j \text{total}(j, t)}{\text{total}(e, t)}} \right]$$

Data source: `performance_history` in profile.yaml + `task_histories` in vessel.py.

### 6.2 Value Network for Task Decomposition

Train a value estimator $V_\theta(s)$ predicting project success probability from partial task tree state, analogous to AlphaGo's value network replacing random rollouts.

### 6.3 RAVE (Rapid Action Value Estimation)

Use AMAF (All Moves As First) heuristic: if employee $e$ performed well on task type $t$ anywhere in the tree, boost $e$'s selection score for $t$-type tasks at all positions.

### 6.4 Organizational Memory as Transposition Table

Build a transposition table mapping (task_description_embedding, team_composition) → (best_decomposition, expected_cost, expected_quality). Reuse proven decomposition strategies for similar tasks without re-searching.

---

## 7. Implementation Reference

| MCTS Phase | Primary Function | File | Line |
|---|---|---|---|
| Selection | EA system prompt + `list_colleagues()` | agents/ea_agent.py, common_tools.py | - |
| Expansion | `TaskTree.add_child()` | task_tree.py | 270 |
| Simulation | `EmployeeManager._execute_task()` | vessel.py | 1170 |
| Backpropagation | `_on_child_complete_inner()` | vessel.py | 2095 |
| Re-planning | `create_iteration()` | project_archive.py | - |
| Pruning | `branch_active` filtering | task_tree.py | 410 |
| Circuit breaker | `_spawn_review_or_escalate()` | vessel.py | 2371 |
| Value update | `_append_history_from_node()` | vessel.py | 1666 |
| Transposition | `schedule_node()` dedup | vessel.py | 724 |
| Recovery | `recover_schedule_from_trees()` | task_persistence.py | 45 |
