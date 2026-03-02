"""Remote Worker Protocol — Pydantic models for HTTP communication.

These models define the data contract between the company server and remote
workers.  Remote workers poll for tasks, execute them, and submit results
via the endpoints defined in ``routes.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RemoteWorkerRegistration(BaseModel):
    """Payload sent by a remote worker when it registers with the company."""

    employee_id: str = Field(description="The employee ID assigned to this worker")
    worker_url: str = Field(description="Callback URL where the worker can be reached")
    capabilities: list[str] = Field(
        default_factory=list,
        description="List of capability tags this worker supports",
    )


class TaskAssignment(BaseModel):
    """A task assigned to a remote worker."""

    task_id: str = Field(description="Unique task identifier")
    project_id: str = Field(description="Project this task belongs to")
    task_description: str = Field(description="Human-readable task description")
    project_dir: str = Field(default="", description="Project workspace directory")
    context: dict = Field(
        default_factory=dict,
        description="Additional context: skills, tools, guidance, etc.",
    )


class TaskResult(BaseModel):
    """Result submitted by a remote worker after processing a task."""

    task_id: str = Field(description="ID of the completed task")
    employee_id: str = Field(description="Employee ID of the worker")
    status: str = Field(
        description="Task outcome: 'completed', 'failed', or 'in_progress'"
    )
    output: str = Field(default="", description="Textual output / summary")
    artifacts: list[dict] = Field(
        default_factory=list,
        description="Files or other artifacts produced by the worker",
    )


class HeartbeatPayload(BaseModel):
    """Keep-alive heartbeat sent periodically by a remote worker."""

    employee_id: str = Field(description="Employee ID of the worker")
    status: str = Field(description="Current worker status, e.g. 'idle', 'busy'")
    current_task_id: str | None = Field(
        default=None, description="Task ID currently being processed, if any"
    )
