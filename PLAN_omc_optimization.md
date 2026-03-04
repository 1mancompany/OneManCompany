# OMC 优化计划书 v2 — 借鉴 Salacia 架构模式

> 本文档是 Claude Code 可执行的优化计划，基于对 Salacia 项目的深度分析，
> 结合 OMC 最新代码状态（2026-03-04），提出可落地的改进方案。
>
> **v2 更新**: 反映 OMC 最新架构重构（agent_loop 重写、talent_market、
> CSO/EA agent、Resolution 系统、Sandbox 集成、Hot Reload 等）

---

## 当前架构快照

```
OneManCompany/
├── src/onemancompany/
│   ├── agents/          # 5 个 Agent (HR/COO/EA/CSO/通用员工)
│   │   ├── base.py      # BaseAgentRunner (有 run_streamed, tracked_ainvoke)
│   │   ├── hr_agent.py  # Boss Online MCP + 招聘 + 绩效
│   │   ├── coo_agent.py # 资产管理 + 会议室
│   │   ├── ea_agent.py  # 任务路由 + 验收标准
│   │   ├── cso_agent.py # 销售管线 + 合同管理
│   │   ├── onboarding.py
│   │   └── common_tools.py
│   ├── core/
│   │   ├── agent_loop.py    # EmployeeManager (1439 行, 重写后)
│   │   ├── state.py         # CompanyState (dataclass, ~250 行)
│   │   ├── events.py        # EventBus (asyncio.Queue, 30+ 事件)
│   │   ├── config.py        # Settings + EmployeeConfig (Pydantic)
│   │   ├── routine.py       # Workflow-driven 会后评审
│   │   ├── resolutions.py   # 文件编辑批量审批
│   │   ├── project_archive.py  # v1/v2 项目归档
│   │   ├── model_costs.py   # OpenRouter 定价 + 薪资计算
│   │   └── workflow_engine.py  # Markdown workflow 解析
│   ├── talent_market/       # MCP 人才市场 + 远程协议
│   │   ├── boss_online.py
│   │   ├── remote_protocol.py
│   │   ├── remote_worker_base.py
│   │   └── talents/{artist,claude_code,coding,...}
│   ├── tools/sandbox/       # OpenSandbox 代码执行
│   └── api/routes.py        # FastAPI + WebSocket
├── frontend/                # Canvas 2D 像素风 (app.js ~162KB)
├── company/                 # YAML 持久化层
└── config.yaml              # Hot-reload 配置
```

**已有亮点** (v1 计划中提到的部分已存在):
- EmployeeConfig 已用 Pydantic BaseModel
- BaseAgentRunner 已有 `run_streamed()` + `tracked_ainvoke()`
- 事件系统已有 EventType enum
- 成本追踪已接入 OpenRouter API
- 3 种 hosting 模式 (company/self/remote) + Launcher 协议
- Resolution 批量审批系统
- Workflow Engine (Markdown DSL)
- Hot Reload + File Watcher

---

## 差距矩阵 (v2 更新)

| 维度 | OMC 现状 | Salacia 做法 | 差距 | 证据 |
|------|---------|-------------|------|------|
| 数据模型 | `dataclass` Employee + `dict` payload | Zod schema → 类型推导 + AJV 验证 | **高** | `performance_history: list[dict]`, `overhead_costs: dict` |
| 事件 payload | `CompanyEvent.payload: dict` 无类型 | 每种事件有独立 schema | **高** | `events.py:41` payload 是裸 dict |
| 错误处理 | 宽泛 `except Exception` + `traceback.print_exc()` | 3 级 fallback + 诊断追踪 + 永不崩溃 | **高** | `agent_loop.py:620` `f"Error: {e!s}"` 丢失上下文 |
| 测试 | **零测试文件** | Vitest unit/integration/e2e | **严重** | 无 `tests/` 目录 |
| 任务生命周期 | 隐式状态转换 (dict 字段存在性判断) | 合约驱动 + 显式状态机 | **高** | `agent_loop.py:1046-1139` 93 行嵌套 if-else |
| Prompt 构建 | 4 个 Agent 复制相同的 8 段拼接 | PromptBuilder 可组合 | **中** | hr/coo/ea/cso 都有相同的 `_build_prompt()` |
| 工具返回值 | `{"status": "ok/error", ...}` 裸 dict | Union[Success, Error] 类型 | **中** | `common_tools.py:70` |
| 审计追踪 | `activity_log` 内存列表 | 不可变证据链 + SHA256 寻址 | **中** | 重启后 activity_log 丢失 |
| 候选人验证 | `talent.get("id", "unknown")` 无校验 | Pydantic 验证 + 错误诊断 | **中** | `hr_agent.py:72` |
| 成本追踪 | 可变 dict 直接 `+=` | 不可变 CostRecord + 审计链 | **中** | `base.py:84` `oh["total_cost_usd"] += cost_usd` |
| 项目归档 | `project.get("acceptance_result")` 裸 dict | 类型化 Project/Iteration 模型 | **中** | `project_archive.py:1098` |
| Resolution | `decision: None/"approved"/"rejected"` 字符串 | Enum + 状态机 | **低-中** | `resolutions.py:60` |

---

## Phase 1: 核心数据模型类型化 [严重优先级]

> **借鉴 Salacia**: Zod schema → 类型推导; 所有数据边界做验证
>
> **现实**: OMC 的 `EmployeeConfig` 已用 Pydantic，但 `Employee` dataclass、
> Event payload、Project archive 仍是裸 dict。这是最大的技术债。

### Task 1.1: Employee 模型从 dataclass → Pydantic

**问题代码** (`state.py`):
```python
@dataclass
class Employee:
    performance_history: list[dict] = field(default_factory=list)  # 无 schema
    overhead_costs: dict  # 完全无类型
```

**改造** — 新建 `src/onemancompany/core/models.py`:

```python
from pydantic import BaseModel, Field, field_validator
from enum import Enum
from typing import Literal, Optional
from datetime import datetime

# === 枚举常量 (取代 config.py 散落的魔法数字) ===

class EmployeeRole(str, Enum):
    HR = "Human Resources"
    COO = "Chief Operating Officer"
    EA = "Executive Assistant"
    CSO = "Chief Sales Officer"
    ENGINEER = "Engineer"
    DESIGNER = "Designer"
    ARTIST = "Artist"

class Department(str, Enum):
    HR = "HR"
    OPERATIONS = "Operations"
    ENGINEERING = "Engineering"
    DESIGN = "Design"
    SALES = "Sales"
    EXECUTIVE = "Executive"

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"
    RECTIFICATION = "rectification"

class PerformanceScore(float, Enum):
    NEEDS_IMPROVEMENT = 3.25
    QUALIFIED = 3.5
    EXCELLENT = 3.75

class DecisionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"

class HostingMode(str, Enum):
    COMPANY = "company"
    SELF = "self"
    REMOTE = "remote"

# === 核心模型 ===

class PerformanceRecord(BaseModel):
    quarter: int
    score: PerformanceScore
    tasks_completed: int
    reviewer: str
    notes: str = ""
    recorded_at: datetime = Field(default_factory=datetime.now)

class CostRecord(BaseModel):
    """单次 LLM 调用的成本记录 (不可变)"""
    timestamp: datetime = Field(default_factory=datetime.now)
    category: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    task_id: str | None = None
    employee_id: str | None = None

class OverheadCosts(BaseModel):
    """成本追踪 — 追加式，不直接修改"""
    records: list[CostRecord] = Field(default_factory=list)

    def add(self, record: CostRecord) -> None:
        self.records.append(record)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.records)

    @property
    def total_tokens(self) -> int:
        return sum(r.input_tokens + r.output_tokens for r in self.records)

    def by_category(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for r in self.records:
            result[r.category] = result.get(r.category, 0.0) + r.cost_usd
        return result

# === Agent 执行结果 ===

class AgentResult(BaseModel):
    success: bool
    output: str
    artifacts: list[str] = []
    tool_calls_count: int = 0
    tokens_used: int = 0
    cost_usd: float = Field(ge=0.0, default=0.0)
    error: str | None = None
    attempt: int = 1
    duration_seconds: float = 0.0

# === Project 模型 ===

class TimelineEntry(BaseModel):
    time: datetime = Field(default_factory=datetime.now)
    employee_id: str
    action: str
    detail: str

class ProjectIteration(BaseModel):
    id: str
    task: str
    status: TaskStatus = TaskStatus.PENDING
    acceptance_criteria: list[str] = []
    timeline: list[TimelineEntry] = []
    output: str = ""
    cost_usd: float = 0.0
    tokens_used: int = 0

class Project(BaseModel):
    id: str
    name: str
    slug: str
    created_at: datetime = Field(default_factory=datetime.now)
    iterations: list[ProjectIteration] = []
    workspace_path: str = ""
```

**改造范围**:
- `state.py` — `Employee.performance_history` → `list[PerformanceRecord]`
- `state.py` — `CompanyState.overhead_costs` → `OverheadCosts`
- `agents/base.py` — `_record_overhead()` 使用 `CostRecord`
- `project_archive.py` — `load_project()` 返回 `Project` 模型

**验收标准**:
- [ ] `performance_history` 不再是 `list[dict]`
- [ ] `overhead_costs` 不再是裸 `dict`
- [ ] mypy 无 `Any` 类型在核心模型中
- [ ] 现有 API 端点不退化

---

### Task 1.2: 事件 Payload 类型化

**问题代码** (`events.py`):
```python
@dataclass
class CompanyEvent:
    payload: dict  # 任何 dict 都能塞进来
```

**改造**:

```python
# src/onemancompany/core/event_models.py

from pydantic import BaseModel
from typing import Union
from onemancompany.core.models import TaskStatus

class TaskStartedPayload(BaseModel):
    task_id: str
    employee_id: str
    description: str

class TaskCompletedPayload(BaseModel):
    task_id: str
    employee_id: str
    success: bool
    output_summary: str
    cost_usd: float = 0.0

class AgentThinkingPayload(BaseModel):
    employee_id: str
    content: str
    tool_name: str | None = None

class CandidatesReadyPayload(BaseModel):
    batch_id: str
    jd: str
    candidates: list[dict]  # 后续迁移到 CandidateProfile

class ResolutionReadyPayload(BaseModel):
    resolution_id: str
    edit_count: int
    project_id: str | None = None

class MeetingChatPayload(BaseModel):
    meeting_id: str
    speaker_id: str
    speaker_name: str
    content: str

class StateSnapshotPayload(BaseModel):
    """空 payload — 触发前端全量刷新"""
    pass

# 联合类型 — 对应 EventType 枚举
EventPayload = Union[
    TaskStartedPayload,
    TaskCompletedPayload,
    AgentThinkingPayload,
    CandidatesReadyPayload,
    ResolutionReadyPayload,
    MeetingChatPayload,
    StateSnapshotPayload,
    dict,  # 过渡期兼容: 未迁移的事件仍用 dict
]
```

**改造策略 (渐进式)**:
1. 先定义所有 Payload 模型
2. 在 `events.py` 中 `publish()` 接受 `EventPayload`
3. 逐个迁移 agent 中的 `publish("xxx", {...})` 调用
4. 最后移除 `dict` 兼容

---

### Task 1.3: Resolution 和 Workflow 类型化

**问题代码** (`resolutions.py:60`):
```python
resolution_edits.append({
    "decision": None,  # None | "approved" | "rejected" — 无约束
    "decided_at": None,
})
```

**改造**:
```python
class FileEditProposal(BaseModel):
    edit_id: str
    file_path: str
    content: str
    original_hash: str | None = None
    decision: DecisionStatus = DecisionStatus.PENDING
    decided_at: datetime | None = None
    executed: bool = False

class Resolution(BaseModel):
    id: str
    project_id: str | None = None
    employee_id: str
    edits: list[FileEditProposal]
    created_at: datetime = Field(default_factory=datetime.now)
    status: DecisionStatus = DecisionStatus.PENDING
```

---

## Phase 2: 任务生命周期状态机 [高优先级]

> **借鉴 Salacia**: Contract 定义范围 + 显式状态机 + Drift Detection
>
> **现实**: `agent_loop.py:1046-1139` 用 93 行嵌套 if-else
> 通过检查 dict 字段存在性来推断任务状态，极易出错。

### Task 2.1: 显式任务状态机

**问题**: 任务生命周期散落在 `_post_task_cleanup()` 的条件分支里：
```python
# agent_loop.py — 隐式状态判断
if acceptance_criteria and not acceptance_result:     # 状态 A
elif acceptance_result.get("accepted"):               # 状态 B
    if not ea_review_result:                          # 状态 C
    if ea_review_result.get("approved"):              # 状态 D
```

**改造**:

```python
# src/onemancompany/core/task_lifecycle.py

from enum import Enum
from pydantic import BaseModel

class TaskPhase(str, Enum):
    """显式任务阶段 — 不再靠 dict 字段推断"""
    CREATED = "created"               # CEO 提交
    ROUTED = "routed"                 # EA 路由到目标 Agent
    IN_PROGRESS = "in_progress"       # Agent 执行中
    COMPLETED = "completed"           # Agent 返回结果
    NEEDS_ACCEPTANCE = "needs_acceptance"  # 等待 COO 验收
    ACCEPTED = "accepted"             # COO 验收通过
    REJECTED_BY_COO = "rejected_by_coo"  # COO 验收不通过
    EA_REVIEW = "ea_review"           # EA 最终审核
    EA_APPROVED = "ea_approved"       # EA 审核通过
    EA_REJECTED = "ea_rejected"       # EA 驳回 → 整改
    RECTIFICATION = "rectification"   # 整改中
    CEO_APPROVAL = "ceo_approval"     # 等待 CEO 审批 (Resolution)
    SETTLED = "settled"               # 完成归档

# 合法状态转换表
VALID_TRANSITIONS: dict[TaskPhase, list[TaskPhase]] = {
    TaskPhase.CREATED: [TaskPhase.ROUTED],
    TaskPhase.ROUTED: [TaskPhase.IN_PROGRESS],
    TaskPhase.IN_PROGRESS: [TaskPhase.COMPLETED, TaskPhase.NEEDS_ACCEPTANCE],
    TaskPhase.COMPLETED: [TaskPhase.NEEDS_ACCEPTANCE, TaskPhase.EA_REVIEW, TaskPhase.SETTLED],
    TaskPhase.NEEDS_ACCEPTANCE: [TaskPhase.ACCEPTED, TaskPhase.REJECTED_BY_COO],
    TaskPhase.ACCEPTED: [TaskPhase.EA_REVIEW, TaskPhase.SETTLED],
    TaskPhase.REJECTED_BY_COO: [TaskPhase.RECTIFICATION],
    TaskPhase.EA_REVIEW: [TaskPhase.EA_APPROVED, TaskPhase.EA_REJECTED],
    TaskPhase.EA_APPROVED: [TaskPhase.CEO_APPROVAL, TaskPhase.SETTLED],
    TaskPhase.EA_REJECTED: [TaskPhase.RECTIFICATION],
    TaskPhase.RECTIFICATION: [TaskPhase.IN_PROGRESS],
    TaskPhase.CEO_APPROVAL: [TaskPhase.SETTLED],
}

class TaskTransitionError(Exception):
    def __init__(self, task_id: str, current: TaskPhase, attempted: TaskPhase):
        self.task_id = task_id
        self.current = current
        self.attempted = attempted
        super().__init__(
            f"Task {task_id}: 非法状态转换 {current.value} → {attempted.value}. "
            f"合法目标: {[t.value for t in VALID_TRANSITIONS.get(current, [])]}"
        )

def transition(task_id: str, current: TaskPhase, target: TaskPhase) -> TaskPhase:
    """验证并执行状态转换"""
    valid = VALID_TRANSITIONS.get(current, [])
    if target not in valid:
        raise TaskTransitionError(task_id, current, target)
    return target
```

**收益**: `_post_task_cleanup()` 从 93 行 if-else 变成声明式状态转换调用。

---

### Task 2.2: 任务合约 (轻量版)

**与 Salacia 的 Contract 对应**, 但适配 OMC 的 CEO → Agent 场景:

```python
# src/onemancompany/core/task_contract.py

class TaskContract(BaseModel):
    """每个任务执行前由 EA 生成的合约"""
    task_id: str
    title: str

    # 意图 (EA 解析 CEO 输入)
    goals: list[str]
    constraints: list[str] = []

    # 范围
    assigned_agent: str
    allowed_tools: list[str] = []
    protected_paths: list[str] = Field(
        default_factory=lambda: [".env", "config.yaml", "company/human_resource/"]
    )

    # 护栏
    max_cost_usd: float = 1.0
    max_iterations: int = 5
    require_ceo_approval: bool = False
    risk_level: Literal["low", "medium", "high"] = "low"

    # 验收
    acceptance_criteria: list[str] = []
    verification_commands: list[str] = []  # 自动验收脚本

    created_at: datetime = Field(default_factory=datetime.now)
```

**集成点**: EA Agent 在路由任务时生成 `TaskContract`，存入 `company/business/contracts/{task_id}.yaml`。

---

### Task 2.3: 执行后漂移检测

```python
# src/onemancompany/core/drift_detector.py

class ViolationCode(str, Enum):
    COST_EXCEEDED = "cost_exceeded"
    PROTECTED_PATH = "protected_path"
    SCOPE_DRIFT = "scope_drift"
    ITERATION_EXCEEDED = "iteration_exceeded"
    UNAUTHORIZED_TOOL = "unauthorized_tool"

class DriftViolation(BaseModel):
    code: ViolationCode
    severity: Literal["low", "medium", "high"]
    message: str
    detail: str = ""

class DriftResult(BaseModel):
    score: int = Field(ge=0, le=100)
    violations: list[DriftViolation]

    @property
    def safe(self) -> bool:
        return self.score < 60

async def detect_drift(
    contract: TaskContract,
    result: AgentResult,
    proposed_edits: list[str] | None = None,
) -> DriftResult:
    """
    借鉴 Salacia drift.ts 的多维评分:
    - high = 45 分, medium = 25 分, low = 10 分
    """
    violations: list[DriftViolation] = []

    # 1. 成本漂移
    if result.cost_usd > contract.max_cost_usd:
        violations.append(DriftViolation(
            code=ViolationCode.COST_EXCEEDED,
            severity="high",
            message=f"花费 ${result.cost_usd:.4f} 超出预算 ${contract.max_cost_usd:.4f}",
        ))

    # 2. 迭代次数漂移
    if result.attempt > contract.max_iterations:
        violations.append(DriftViolation(
            code=ViolationCode.ITERATION_EXCEEDED,
            severity="medium",
            message=f"执行 {result.attempt} 次，超出限制 {contract.max_iterations} 次",
        ))

    # 3. 受保护路径
    for path in (proposed_edits or []):
        if any(path.startswith(p) for p in contract.protected_paths):
            violations.append(DriftViolation(
                code=ViolationCode.PROTECTED_PATH,
                severity="high",
                message=f"试图修改受保护路径: {path}",
            ))

    score = min(100, sum(
        45 if v.severity == "high" else 25 if v.severity == "medium" else 10
        for v in violations
    ))
    return DriftResult(score=score, violations=violations)
```

---

## Phase 3: 结构化错误处理 [高优先级]

> **借鉴 Salacia**: 永不崩溃 + 3 级 fallback + parseStatus 追踪
>
> **现实**: `agent_loop.py:620` 把所有异常压缩成 `f"Error: {e!s}"`，
> 前端和调试者无法区分是 LLM rate limit、工具权限错误、还是递归溢出。

### Task 3.1: 结构化错误类型

```python
# src/onemancompany/core/errors.py

from enum import Enum
from pydantic import BaseModel

class ErrorCode(str, Enum):
    # Agent 执行
    AGENT_TIMEOUT = "agent_timeout"
    AGENT_RECURSION_LIMIT = "agent_recursion_limit"
    AGENT_TOOL_FAILURE = "agent_tool_failure"
    AGENT_EMPTY_RESPONSE = "agent_empty_response"

    # LLM 提供商
    LLM_RATE_LIMIT = "llm_rate_limit"
    LLM_AUTH_FAILURE = "llm_auth_failure"
    LLM_CONTEXT_OVERFLOW = "llm_context_overflow"
    LLM_PROVIDER_DOWN = "llm_provider_down"

    # 业务逻辑
    MEETING_ROOM_UNAVAILABLE = "meeting_room_unavailable"
    EMPLOYEE_NOT_FOUND = "employee_not_found"
    BUDGET_EXCEEDED = "budget_exceeded"
    PERMISSION_DENIED = "permission_denied"
    CONTRACT_VIOLATION = "contract_violation"

    # 系统
    FILE_IO_ERROR = "file_io_error"
    STATE_CORRUPTION = "state_corruption"
    WEBSOCKET_ERROR = "websocket_error"

class StructuredError(BaseModel):
    code: ErrorCode
    severity: Literal["warning", "error", "critical"]
    message: str
    suggestion: str  # 可操作的修复建议
    context: dict = {}
    recoverable: bool = True

def classify_exception(exc: Exception) -> StructuredError:
    """
    借鉴 Salacia parseAdvisorResponse 的分层解析:
    从异常类型 + 消息推断结构化错误码
    """
    msg = str(exc).lower()

    if isinstance(exc, asyncio.TimeoutError):
        return StructuredError(
            code=ErrorCode.AGENT_TIMEOUT,
            severity="error",
            message=f"Agent 执行超时: {exc}",
            suggestion="考虑简化任务描述或增加超时时间",
            recoverable=True,
        )
    if "GraphRecursionError" in type(exc).__name__:
        return StructuredError(
            code=ErrorCode.AGENT_RECURSION_LIMIT,
            severity="error",
            message=f"Agent 递归次数达到上限: {exc}",
            suggestion="任务可能过于复杂，建议拆分为子任务",
            recoverable=True,
        )
    if "rate_limit" in msg or "429" in msg:
        return StructuredError(
            code=ErrorCode.LLM_RATE_LIMIT,
            severity="warning",
            message=f"LLM 请求频率超限: {exc}",
            suggestion="等待 60 秒后自动重试",
            recoverable=True,
        )
    if "auth" in msg or "401" in msg or "403" in msg:
        return StructuredError(
            code=ErrorCode.LLM_AUTH_FAILURE,
            severity="critical",
            message=f"LLM 认证失败: {exc}",
            suggestion="检查 API key 配置",
            recoverable=False,
        )
    # 兜底
    return StructuredError(
        code=ErrorCode.AGENT_TOOL_FAILURE,
        severity="error",
        message=f"未分类错误: {exc}",
        suggestion="查看日志获取详细信息",
        context={"exception_type": type(exc).__name__},
        recoverable=True,
    )
```

### Task 3.2: Agent 执行 3 级降级

**改造 `agent_loop.py` 中的执行逻辑**:

```python
# src/onemancompany/core/safe_execute.py

async def safe_agent_execute(
    launcher: Launcher,
    employee_id: str,
    task: AgentTask,
    timeout: float = 120.0,
) -> tuple[AgentResult, list[StructuredError]]:
    """
    借鉴 Salacia 的 runExternalAdvisor 三级重试:
    Tier 1: 正常执行
    Tier 2: 简化任务 + 缩短超时
    Tier 3: 结构化失败 (永不崩溃)

    返回 (result, diagnostics) — 即使失败也返回结果
    """
    diagnostics: list[StructuredError] = []

    # Tier 1: 正常执行
    try:
        result = await asyncio.wait_for(
            launcher.run(employee_id, task),
            timeout=timeout,
        )
        return result, diagnostics
    except Exception as e1:
        err = classify_exception(e1)
        diagnostics.append(err)
        if not err.recoverable:
            return AgentResult(success=False, output="", error=err.message, attempt=1), diagnostics

    # Tier 2: 简化重试 (截断描述 + 缩短超时)
    try:
        simplified = task.copy()
        simplified.description = f"[简化] {task.description[:500]}"
        result = await asyncio.wait_for(
            launcher.run(employee_id, simplified),
            timeout=timeout / 2,
        )
        diagnostics.append(StructuredError(
            code=ErrorCode.AGENT_TOOL_FAILURE,
            severity="warning",
            message="Tier 1 失败，Tier 2 简化执行成功",
            suggestion="原始任务可能过于复杂",
        ))
        result.attempt = 2
        return result, diagnostics
    except Exception as e2:
        diagnostics.append(classify_exception(e2))

    # Tier 3: 结构化失败 (永不抛异常)
    return AgentResult(
        success=False,
        output="",
        error=f"3 级降级后仍失败: {diagnostics[-1].message}",
        attempt=3,
    ), diagnostics
```

---

## Phase 4: Prompt 构建去重 [中优先级]

> **借鉴 Salacia**: PromptBuilder 可组合 sections
>
> **现实**: 4 个 Agent 各有一模一样的 `_build_prompt()`:
> `HR_SYSTEM_PROMPT + skills + tools + culture + principles + guidance + context + efficiency`

### Task 4.1: 可组合 PromptBuilder

```python
# src/onemancompany/agents/prompt_builder.py

from dataclasses import dataclass, field

@dataclass
class PromptSection:
    name: str
    content: str
    priority: int = 50  # 用于排序: 越小越靠前

class PromptBuilder:
    """
    借鉴 Salacia 的 compilePromptInput:
    - 可组合的 section 系统
    - 子类只需 add_section() 覆盖特定段
    - 自动按 priority 排序
    """

    def __init__(self):
        self._sections: dict[str, PromptSection] = {}

    def add(self, name: str, content: str, priority: int = 50) -> "PromptBuilder":
        if content.strip():
            self._sections[name] = PromptSection(name=name, content=content, priority=priority)
        return self

    def remove(self, name: str) -> "PromptBuilder":
        self._sections.pop(name, None)
        return self

    def build(self) -> str:
        sections = sorted(self._sections.values(), key=lambda s: s.priority)
        return "\n\n".join(s.content for s in sections)


# 在 BaseAgentRunner 中使用:
class BaseAgentRunner:
    def build_prompt(self) -> str:
        pb = PromptBuilder()
        pb.add("role", self._get_role_prompt(), priority=10)
        pb.add("skills", self._get_skills_prompt_section(), priority=20)
        pb.add("tools", self._get_tools_prompt_section(), priority=30)
        pb.add("culture", self._get_company_culture_prompt_section(), priority=40)
        pb.add("principles", self._get_work_principles_prompt_section(), priority=50)
        pb.add("guidance", self._get_guidance_prompt_section(), priority=60)
        pb.add("context", self._get_dynamic_context_section(), priority=70)
        pb.add("efficiency", self._get_efficiency_guidelines_section(), priority=80)

        # 子类可以覆写添加/删除 sections
        self._customize_prompt(pb)
        return pb.build()

    def _customize_prompt(self, pb: PromptBuilder) -> None:
        """子类覆写此方法来定制 prompt"""
        pass
```

**收益**: 新 Agent 只需覆写 `_get_role_prompt()` 和 `_customize_prompt()`。

---

## Phase 5: 证据链与审计 [中优先级]

> **借鉴 Salacia**: `.salacia/journal/` + SHA256 内容寻址 + 不可变记录

### Task 5.1: Journal 系统

```python
# src/onemancompany/core/journal.py

import hashlib
from pathlib import Path
from pydantic import BaseModel
from datetime import datetime

class EvidenceKind(str, Enum):
    TASK_DISPATCH = "task_dispatch"
    TASK_COMPLETED = "task_completed"
    TOOL_CALL = "tool_call"
    RESOLUTION_DECIDED = "resolution_decided"
    PERFORMANCE_REVIEW = "performance_review"
    COST_RECORDED = "cost_recorded"
    DRIFT_DETECTED = "drift_detected"
    STATE_TRANSITION = "state_transition"

class EvidenceRecord(BaseModel):
    kind: EvidenceKind
    agent: str
    task_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    payload: dict  # kind-specific data

class Journal:
    """
    借鉴 Salacia evidence.ts:
    - 不可变 append-only 记录
    - SHA256 摘要作为文件名后缀 (防碰撞)
    - 按 agent/kind 分目录
    """

    def __init__(self, base_dir: str = "company/journal"):
        self.base = Path(base_dir)

    def write_sync(self, record: EvidenceRecord) -> str:
        dir_path = self.base / record.agent
        dir_path.mkdir(parents=True, exist_ok=True)

        content = record.model_dump_json(indent=2)
        digest = hashlib.sha256(content.encode()).hexdigest()[:12]
        ts = int(record.created_at.timestamp())
        filename = f"{record.kind.value}-{ts}-{digest}.json"

        file_path = dir_path / filename
        file_path.write_text(content, encoding="utf-8")
        return str(file_path)

    def query(
        self,
        agent: str | None = None,
        kind: EvidenceKind | None = None,
        limit: int = 100,
    ) -> list[EvidenceRecord]:
        search_dir = self.base / agent if agent else self.base
        if not search_dir.exists():
            return []

        results = []
        for f in sorted(search_dir.rglob("*.json"), reverse=True):
            if kind and not f.name.startswith(kind.value):
                continue
            try:
                record = EvidenceRecord.model_validate_json(f.read_text())
                results.append(record)
            except Exception:
                continue  # 跳过损坏的记录
            if len(results) >= limit:
                break
        return results

# 全局单例
journal = Journal()
```

**注入点**:
- `agent_loop.py` — 任务开始/完成时写 `task_dispatch`/`task_completed`
- `base.py` — `_record_overhead()` 同时写 `cost_recorded`
- `resolutions.py` — CEO 决策时写 `resolution_decided`
- `routine.py` — 绩效评审写 `performance_review`
- `drift_detector.py` — 违规时写 `drift_detected`
- `task_lifecycle.py` — 状态转换写 `state_transition`

---

## Phase 6: 测试基础设施 [严重优先级]

> **借鉴 Salacia**: Vitest 三级测试 (unit / integration / e2e)
>
> **现实**: **零测试**。这是最危险的技术债。

### Task 6.1: 测试框架搭建

```
tests/
├── conftest.py               # 共享 fixtures
├── unit/
│   ├── test_models.py        # Pydantic 模型验证
│   ├── test_event_models.py  # 事件 payload 验证
│   ├── test_task_lifecycle.py # 状态机转换
│   ├── test_drift_detector.py # 漂移检测
│   ├── test_errors.py        # 异常分类
│   ├── test_prompt_builder.py # Prompt 构建
│   ├── test_journal.py       # Journal 读写
│   ├── test_intent_parser.py # CEO 输入解析
│   └── test_model_costs.py   # 成本计算
├── integration/
│   ├── test_agent_loop.py    # Agent 执行 (mock LLM)
│   ├── test_workflow_engine.py # Workflow 解析 + 调度
│   ├── test_project_archive.py # 项目归档 CRUD
│   ├── test_resolutions.py   # Resolution 生命周期
│   └── test_state_persistence.py # 状态保存/恢复
└── e2e/
    ├── test_websocket.py     # WebSocket 连接 + 事件接收
    └── test_ceo_task_flow.py # CEO → EA → Agent → Resolution
```

**pyproject.toml 配置**:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "unit: 单元测试 (无外部依赖, <1s)",
    "integration: 集成测试 (mock LLM, <30s)",
    "e2e: 端到端测试 (需要服务运行, <120s)",
]
asyncio_mode = "auto"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-timeout>=2.3",
    "respx>=0.22",  # HTTP mock
]
```

### Task 6.2: 核心测试用例

```python
# tests/conftest.py
import pytest
from onemancompany.core.models import (
    PerformanceRecord, PerformanceScore, CostRecord, AgentResult,
    TaskStatus, OverheadCosts,
)
from onemancompany.core.task_lifecycle import TaskPhase, transition

@pytest.fixture
def sample_cost_record():
    return CostRecord(
        category="agent_task",
        model="anthropic/claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.012,
        employee_id="00002",
    )

# tests/unit/test_models.py
class TestPerformanceRecord:
    def test_valid_score(self):
        record = PerformanceRecord(
            quarter=1, score=PerformanceScore.EXCELLENT,
            tasks_completed=3, reviewer="00002",
        )
        assert record.score == 3.75

    def test_invalid_score_rejected(self):
        with pytest.raises(ValidationError):
            PerformanceRecord(
                quarter=1, score=4.0,  # 非法分数
                tasks_completed=3, reviewer="00002",
            )

class TestOverheadCosts:
    def test_add_and_total(self, sample_cost_record):
        costs = OverheadCosts()
        costs.add(sample_cost_record)
        assert costs.total_cost_usd == 0.012
        assert costs.total_tokens == 1500

    def test_by_category(self, sample_cost_record):
        costs = OverheadCosts()
        costs.add(sample_cost_record)
        assert costs.by_category() == {"agent_task": 0.012}

class TestAgentResult:
    def test_cost_non_negative(self):
        with pytest.raises(ValidationError):
            AgentResult(success=True, output="ok", cost_usd=-0.5)

# tests/unit/test_task_lifecycle.py
class TestTaskLifecycle:
    def test_valid_transition(self):
        result = transition("t1", TaskPhase.CREATED, TaskPhase.ROUTED)
        assert result == TaskPhase.ROUTED

    def test_invalid_transition_raises(self):
        with pytest.raises(TaskTransitionError) as exc_info:
            transition("t1", TaskPhase.CREATED, TaskPhase.SETTLED)
        assert "非法状态转换" in str(exc_info.value)
        assert "created → settled" in str(exc_info.value)

    def test_full_happy_path(self):
        """CEO → EA → Agent → COO → EA → CEO → Settled"""
        phases = [
            TaskPhase.CREATED,
            TaskPhase.ROUTED,
            TaskPhase.IN_PROGRESS,
            TaskPhase.COMPLETED,
            TaskPhase.NEEDS_ACCEPTANCE,
            TaskPhase.ACCEPTED,
            TaskPhase.EA_REVIEW,
            TaskPhase.EA_APPROVED,
            TaskPhase.CEO_APPROVAL,
            TaskPhase.SETTLED,
        ]
        current = phases[0]
        for target in phases[1:]:
            current = transition("t1", current, target)
        assert current == TaskPhase.SETTLED

# tests/unit/test_drift_detector.py
class TestDriftDetector:
    @pytest.mark.asyncio
    async def test_cost_exceeded(self):
        contract = TaskContract(
            task_id="t1", title="test", goals=["test"],
            max_cost_usd=0.5,
        )
        result = AgentResult(success=True, output="ok", cost_usd=1.2)
        drift = await detect_drift(contract, result)
        assert not drift.safe
        assert any(v.code == ViolationCode.COST_EXCEEDED for v in drift.violations)

    @pytest.mark.asyncio
    async def test_protected_path_blocked(self):
        contract = TaskContract(
            task_id="t1", title="test", goals=["test"],
            protected_paths=[".env", "config.yaml"],
        )
        result = AgentResult(success=True, output="ok")
        drift = await detect_drift(contract, result, proposed_edits=[".env"])
        assert any(v.code == ViolationCode.PROTECTED_PATH for v in drift.violations)
        assert drift.score >= 45  # high severity = 45 分

    @pytest.mark.asyncio
    async def test_clean_execution(self):
        contract = TaskContract(
            task_id="t1", title="test", goals=["test"],
            max_cost_usd=5.0,
        )
        result = AgentResult(success=True, output="ok", cost_usd=0.1)
        drift = await detect_drift(contract, result)
        assert drift.safe
        assert drift.score == 0

# tests/unit/test_errors.py
class TestErrorClassification:
    def test_timeout(self):
        err = classify_exception(asyncio.TimeoutError())
        assert err.code == ErrorCode.AGENT_TIMEOUT
        assert err.recoverable is True

    def test_rate_limit(self):
        err = classify_exception(Exception("rate_limit_exceeded (429)"))
        assert err.code == ErrorCode.LLM_RATE_LIMIT
        assert err.severity == "warning"

    def test_auth_failure(self):
        err = classify_exception(Exception("Authentication failed (401)"))
        assert err.code == ErrorCode.LLM_AUTH_FAILURE
        assert err.recoverable is False

# tests/unit/test_journal.py
class TestJournal:
    def test_write_and_query(self, tmp_path):
        j = Journal(base_dir=str(tmp_path))
        record = EvidenceRecord(
            kind=EvidenceKind.TASK_COMPLETED,
            agent="hr_agent",
            task_id="t1",
            payload={"success": True, "output": "hired"},
        )
        path = j.write_sync(record)
        assert Path(path).exists()

        results = j.query(agent="hr_agent", kind=EvidenceKind.TASK_COMPLETED)
        assert len(results) == 1
        assert results[0].task_id == "t1"
```

---

## Phase 7: 候选人数据验证 + 工具返回类型 [中优先级]

### Task 7.1: Talent / Candidate 模型

```python
# src/onemancompany/talent_market/models.py

class TalentProfile(BaseModel):
    """从 talent_market/talents/*/profile.yaml 加载"""
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str
    skills: list[str] = Field(min_length=1)
    hosting: HostingMode = HostingMode.COMPANY
    llm_model: str = ""
    temperature: float = Field(ge=0.0, le=2.0, default=0.7)
    salary_per_1m_tokens: float = Field(ge=0.0, default=0.0)
    hiring_fee: float = Field(ge=0.0, default=0.0)

    @field_validator("id")
    @classmethod
    def id_not_unknown(cls, v: str) -> str:
        if v == "unknown":
            raise ValueError("Talent ID 不能为 'unknown'")
        return v
```

### Task 7.2: 工具返回类型

```python
# src/onemancompany/agents/tool_results.py

class ToolSuccess(BaseModel):
    status: Literal["ok"] = "ok"

class ReadFileResult(ToolSuccess):
    path: str
    content: str

class ToolError(BaseModel):
    status: Literal["error"] = "error"
    message: str
    code: str = "unknown"

ToolResult = Union[ToolSuccess, ToolError]
```

---

## Phase 8: 前端事件协议 + 模块拆分 [低优先级]

### Task 8.1: WebSocket 事件协议文档

基于 Phase 1 的类型化 event payload，生成前端可用的 TypeScript 类型定义:

```typescript
// frontend/types/events.d.ts (自动生成)
interface TaskStartedEvent {
  type: "task_started";
  payload: { task_id: string; employee_id: string; description: string };
}
interface TaskCompletedEvent {
  type: "task_completed";
  payload: { task_id: string; employee_id: string; success: boolean; ... };
}
// ... 30+ 事件
type CompanyEvent = TaskStartedEvent | TaskCompletedEvent | ...;
```

### Task 8.2: app.js 模块拆分

同 v1 计划，按功能拆分 `app.js` 为 ES modules。

---

## 实施路线图 (v2)

```
Sprint 1 (Week 1-2): 地基 — 类型 + 测试
├── Task 1.1: models.py (Pydantic 核心模型)
├── Task 1.2: event_models.py (事件 payload 类型化)
├── Task 6.1: 测试框架 (conftest + 目录结构)
└── Task 6.2: 核心 unit tests (models, lifecycle, errors)

Sprint 2 (Week 3-4): 骨架 — 状态机 + 错误处理
├── Task 2.1: task_lifecycle.py (显式状态机)
├── Task 3.1: errors.py (结构化错误码)
├── Task 3.2: safe_execute.py (3 级降级)
└── 补充测试: test_task_lifecycle, test_errors, test_drift

Sprint 3 (Week 5-6): 肌肉 — 合约 + 证据链
├── Task 2.2: task_contract.py (轻量合约)
├── Task 2.3: drift_detector.py (漂移检测)
├── Task 5.1: journal.py (证据链)
└── 集成: EA 生成合约 → Agent 执行 → 漂移检测 → Journal 记录

Sprint 4 (Week 7-8): 优化 — Prompt + 验证
├── Task 4.1: prompt_builder.py (去重)
├── Task 7.1: talent_market/models.py (候选人验证)
├── Task 7.2: tool_results.py (工具返回类型)
├── Task 1.3: Resolution 类型化
└── Integration + E2E 测试补全

Sprint 5 (Week 9+): 前端 + 长期
├── Task 8.1: 事件协议文档
└── Task 8.2: app.js 模块拆分
```

---

## 改造原则 (v2 更新)

1. **渐进式改造** — 每个 Sprint 独立可交付，不阻塞日常开发
2. **Schema 即类型** — Pydantic 是单一真相源 (对应 Salacia 的 Zod)
3. **永不崩溃** — 3 级降级 + `classify_exception()` (对应 Salacia 的 fallback)
4. **显式状态机** — `TaskPhase` enum + `VALID_TRANSITIONS` (对应 Salacia 的 Contract lifecycle)
5. **证据可审计** — Journal 记录每个关键决策 (对应 Salacia 的 `.salacia/journal/`)
6. **合约护栏** — 成本/路径/迭代上限 (对应 Salacia 的 drift detection)
7. **测试先行** — 新模块必须有 unit test (对应 Salacia 的三级测试)
8. **尊重现有架构** — 不重写 agent_loop.py，而是注入新能力层

---

## 预期收益 (v2 量化)

| 改造 | 投入 | 收益 |
|------|------|------|
| Pydantic Models | ~2 天 | 消除 `list[dict]` 类型盲区; IDE 补全覆盖率 → 95% |
| 事件类型化 | ~2 天 | 前端事件处理 bug 减少 ~80%; 拼写错误编译期发现 |
| 任务状态机 | ~1 天 | `_post_task_cleanup()` 从 93 行 → ~20 行; 非法转换编译期拦截 |
| 结构化错误 | ~1 天 | 前端可区分错误类型; CEO 看到可操作的修复建议 |
| 3 级降级 | ~1 天 | 系统可用性 ~85% → ~98%; 单 Agent 失败不阻塞全局 |
| 任务合约 | ~2 天 | Agent 不再超预算/改错文件; protected_path 硬护栏 |
| 漂移检测 | ~1 天 | 自动标记违规; CEO 审批前可见风险评分 |
| Journal 审计 | ~2 天 | 任何决策可追溯; 调试时间从小时级 → 分钟级 |
| Prompt 去重 | ~1 天 | 新增 Agent 成本从 ~200 行 → ~30 行 |
| 测试框架 | ~3 天 | 回归检测; 重构信心; CI 可信赖 |
| **总计** | **~16 天** | **从原型级 → 工程级** |
