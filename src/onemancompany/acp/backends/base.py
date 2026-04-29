"""ACP Backend Protocol - subprocess execution interface."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AcpBackend(Protocol):
    """Protocol for ACP agent subprocess backends.

    Each backend (langchain, claude_cli, script) implements this protocol
    to handle setup, teardown, and per-task environment configuration
    for executing agent code in isolated subprocesses.
    """

    executor_type: str
    """Backend type identifier: "langchain" | "claude_cli" | "script"."""

    async def setup(self, employee_id: str, config: Any) -> dict[str, str]:
        """One-time setup when backend is initialized.

        Args:
            employee_id: Employee identifier
            config: VesselConfig (passed as Any to avoid circular imports)

        Returns:
            Dictionary of environment variables to export for the subprocess.
        """
        ...

    async def teardown(self, employee_id: str) -> None:
        """Cleanup when employee is unregistered or backend shuts down.

        Args:
            employee_id: Employee identifier
        """
        ...

    def build_task_env(self, context: Any) -> dict[str, str]:
        """Build per-task environment variables.

        Args:
            context: TaskContext (passed as Any to avoid circular imports)

        Returns:
            Dictionary of environment variables for this task execution.
        """
        ...
