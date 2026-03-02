"""Remote Worker Base Class.

Subclass ``RemoteWorkerBase`` to create a remote worker that connects to a
OneManCompany server, registers itself, and processes tasks via the remote
worker HTTP protocol.

See ``README.md`` for a usage guide.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

import httpx

from onemancompany.talent_market.remote_protocol import (
    HeartbeatPayload,
    RemoteWorkerRegistration,
    TaskAssignment,
    TaskResult,
)


class RemoteWorkerBase(ABC):
    """Abstract base class for remote workers.

    Subclass this and implement :meth:`setup_tools` and :meth:`process_task`.

    Example::

        class MyCodingWorker(RemoteWorkerBase):
            def setup_tools(self) -> list:
                return [my_sandbox_tool, my_web_search_tool]

            async def process_task(self, task: TaskAssignment) -> TaskResult:
                # ... do work ...
                return TaskResult(
                    task_id=task.task_id,
                    employee_id=self.employee_id,
                    status="completed",
                    output="Done!",
                )
    """

    def __init__(
        self,
        company_url: str,
        employee_id: str,
        *,
        capabilities: list[str] | None = None,
        heartbeat_interval: float = 30.0,
        poll_interval: float = 5.0,
    ) -> None:
        self.company_url = company_url.rstrip("/")
        self.employee_id = employee_id
        self.capabilities = capabilities or []
        self.heartbeat_interval = heartbeat_interval
        self.poll_interval = poll_interval
        self._running = False
        self._current_task_id: str | None = None

    # ------------------------------------------------------------------
    # Abstract interface — implement these in your subclass
    # ------------------------------------------------------------------

    @abstractmethod
    def setup_tools(self) -> list:
        """Return a list of LangChain tools this worker provides.

        Called once during :meth:`start` before entering the main loop.
        """

    @abstractmethod
    async def process_task(self, task: TaskAssignment) -> TaskResult:
        """Process a single task and return the result.

        Args:
            task: The task assignment received from the company server.

        Returns:
            A :class:`TaskResult` with the outcome.
        """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Register with the company server, then poll for tasks."""
        self.setup_tools()
        await self._register()
        self._running = True
        await asyncio.gather(
            self._poll_loop(),
            self._heartbeat_loop(),
        )

    def stop(self) -> None:
        """Signal the worker to stop after the current iteration."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal loops
    # ------------------------------------------------------------------

    async def _register(self) -> None:
        """POST /api/remote/register to announce this worker."""
        payload = RemoteWorkerRegistration(
            employee_id=self.employee_id,
            worker_url="",  # poll-based — no callback needed
            capabilities=self.capabilities,
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.company_url}/api/remote/register",
                json=payload.model_dump(),
                timeout=10.0,
            )
            resp.raise_for_status()

    async def _poll_loop(self) -> None:
        """GET /api/remote/tasks/{eid}, process, POST result."""
        async with httpx.AsyncClient() as client:
            while self._running:
                try:
                    resp = await client.get(
                        f"{self.company_url}/api/remote/tasks/{self.employee_id}",
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("task"):
                            task = TaskAssignment(**data["task"])
                            self._current_task_id = task.task_id
                            result = await self.process_task(task)
                            self._current_task_id = None
                            await client.post(
                                f"{self.company_url}/api/remote/results",
                                json=result.model_dump(),
                                timeout=30.0,
                            )
                except Exception as exc:  # noqa: BLE001
                    print(f"[RemoteWorker] poll error: {exc}")
                await asyncio.sleep(self.poll_interval)

    async def _heartbeat_loop(self) -> None:
        """POST /api/remote/heartbeat periodically."""
        async with httpx.AsyncClient() as client:
            while self._running:
                try:
                    payload = HeartbeatPayload(
                        employee_id=self.employee_id,
                        status="busy" if self._current_task_id else "idle",
                        current_task_id=self._current_task_id,
                    )
                    await client.post(
                        f"{self.company_url}/api/remote/heartbeat",
                        json=payload.model_dump(),
                        timeout=10.0,
                    )
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(self.heartbeat_interval)
