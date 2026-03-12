"""VesselHarness — 套接件协议定义。

每个 Protocol 定义一类套接件标准，EmployeeManager 内部实现默认版本。
套接件将 Vessel 与公司系统解耦，使执行、任务管理、事件、存储、
上下文注入、生命周期钩子各自可独立替换。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from onemancompany.core.agent_loop import LaunchResult, TaskContext
    from onemancompany.core.events import CompanyEvent
    from onemancompany.core.vessel_config import VesselConfig


# ---------------------------------------------------------------------------
# Execution Harness — Launcher 协议的 vessel 版本
# ---------------------------------------------------------------------------

@runtime_checkable
class ExecutionHarness(Protocol):
    """执行套接件 — 定义如何执行单个任务迭代。"""

    async def execute(
        self,
        task_description: str,
        context: "TaskContext",
        on_log: "((str, str) -> None) | None" = None,
    ) -> "LaunchResult": ...

    def is_ready(self) -> bool: ...


# ---------------------------------------------------------------------------
# Task Harness — 任务队列管理
# ---------------------------------------------------------------------------

@runtime_checkable
class TaskHarness(Protocol):
    """任务套接件 — 任务队列管理。"""

    def schedule_node(
        self, employee_id: str, node_id: str, tree_path: str,
    ) -> None: ...

    def get_next_scheduled(
        self, employee_id: str,
    ) -> "tuple[str, str] | None": ...

    def cancel_by_project(self, project_id: str) -> int: ...


# ---------------------------------------------------------------------------
# Event Harness — 事件发布
# ---------------------------------------------------------------------------

@runtime_checkable
class EventHarness(Protocol):
    """事件套接件 — 事件发布。"""

    async def publish_log(
        self, emp_id: str, node_id: str, log_type: str, content: str,
    ) -> None: ...

    async def publish_task_update(
        self, emp_id: str, node_id: str,
    ) -> None: ...

    async def publish_event(self, event: "CompanyEvent") -> None: ...


# ---------------------------------------------------------------------------
# Storage Harness — 持久化
# ---------------------------------------------------------------------------

@runtime_checkable
class StorageHarness(Protocol):
    """存储套接件 — 持久化。"""

    def append_progress(self, emp_id: str, entry: str) -> None: ...

    def load_progress(self, emp_id: str, max_lines: int = 30) -> str: ...

    def append_history(self, emp_id: str, node_id: str, summary: str) -> None: ...

    def get_history_context(self, emp_id: str) -> str: ...


# ---------------------------------------------------------------------------
# Context Harness — prompt/context 注入
# ---------------------------------------------------------------------------

@runtime_checkable
class ContextHarness(Protocol):
    """上下文套接件 — prompt/context 注入。"""

    def build_task_context(
        self, emp_id: str, node_id: str, tree_path: str, config: "VesselConfig",
    ) -> str: ...

    def get_project_history(self, project_id: str) -> str: ...

    def get_workflow_context(self, emp_id: str, task_description: str) -> str: ...


# ---------------------------------------------------------------------------
# Lifecycle Harness — 钩子调用
# ---------------------------------------------------------------------------

@runtime_checkable
class LifecycleHarness(Protocol):
    """生命周期套接件 — 钩子调用。"""

    def call_pre_task(
        self, emp_id: str, task_text: str, ctx: "TaskContext",
    ) -> str: ...

    def call_post_task(
        self, emp_id: str, node_id: str, result: str,
    ) -> None: ...
