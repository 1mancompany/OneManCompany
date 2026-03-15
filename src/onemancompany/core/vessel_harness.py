"""VesselHarness — Adapter protocol definitions.

Each Protocol defines a category of adapter standards; EmployeeManager implements default versions internally.
Adapters decouple the Vessel from company systems, allowing execution, task management, events, storage,
context injection, and lifecycle hooks to be independently replaceable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from onemancompany.core.agent_loop import LaunchResult, TaskContext
    from onemancompany.core.events import CompanyEvent
    from onemancompany.core.vessel_config import VesselConfig


# ---------------------------------------------------------------------------
# Execution Harness — Vessel version of the Launcher protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ExecutionHarness(Protocol):
    """Execution adapter — defines how to execute a single task iteration."""

    async def execute(
        self,
        task_description: str,
        context: "TaskContext",
        on_log: "((str, str) -> None) | None" = None,
    ) -> "LaunchResult": ...

    def is_ready(self) -> bool: ...


# ---------------------------------------------------------------------------
# Task Harness — Task queue management
# ---------------------------------------------------------------------------

@runtime_checkable
class TaskHarness(Protocol):
    """Task adapter — task queue management."""

    def schedule_node(
        self, employee_id: str, node_id: str, tree_path: str,
    ) -> None: ...

    def get_next_scheduled(
        self, employee_id: str,
    ) -> "tuple[str, str] | None": ...

    def cancel_by_project(self, project_id: str) -> int: ...


# ---------------------------------------------------------------------------
# Event Harness — Event publishing
# ---------------------------------------------------------------------------

@runtime_checkable
class EventHarness(Protocol):
    """Event adapter — event publishing."""

    async def publish_log(
        self, emp_id: str, node_id: str, log_type: str, content: str,
    ) -> None: ...

    async def publish_task_update(
        self, emp_id: str, node_id: str,
    ) -> None: ...

    async def publish_event(self, event: "CompanyEvent") -> None: ...


# ---------------------------------------------------------------------------
# Storage Harness — Persistence
# ---------------------------------------------------------------------------

@runtime_checkable
class StorageHarness(Protocol):
    """Storage adapter — persistence."""

    def append_progress(self, emp_id: str, entry: str) -> None: ...

    def load_progress(self, emp_id: str, max_lines: int = 30) -> str: ...

    def append_history(self, emp_id: str, node_id: str, summary: str) -> None: ...

    def get_history_context(self, emp_id: str) -> str: ...


# ---------------------------------------------------------------------------
# Context Harness — prompt/context injection
# ---------------------------------------------------------------------------

@runtime_checkable
class ContextHarness(Protocol):
    """Context adapter — prompt/context injection."""

    def build_task_context(
        self, emp_id: str, node_id: str, tree_path: str, config: "VesselConfig",
    ) -> str: ...

    def get_project_history(self, project_id: str) -> str: ...

    def get_workflow_context(self, emp_id: str, task_description: str) -> str: ...


# ---------------------------------------------------------------------------
# Lifecycle Harness — Hook invocation
# ---------------------------------------------------------------------------

@runtime_checkable
class LifecycleHarness(Protocol):
    """Lifecycle adapter — hook invocation."""

    def call_pre_task(
        self, emp_id: str, task_text: str, ctx: "TaskContext",
    ) -> str: ...

    def call_post_task(
        self, emp_id: str, node_id: str, result: str,
    ) -> None: ...
