"""SubprocessExecutor — runs employee tasks as bash subprocesses.

Each company-hosted employee runs via launch.sh. Cancel = OS-level kill.

Prompt delivery: written to a temp file and passed via OMC_TASK_DESCRIPTION_FILE
env var. The launch.sh reads from this file. This avoids OS limits on env var
length (typically ~128KB on macOS, ~2MB on Linux) which conversation prompts
with history can easily exceed.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Callable

from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR, LAUNCH_SH_FILENAME
from onemancompany.core.vessel import Launcher, LaunchResult, TaskContext

_KILL_POLL_INTERVAL = 5
_KILL_GRACE_PERIOD = 30


class SubprocessExecutor(Launcher):
    """Execute employee tasks via bash subprocess with OS-level cancel."""

    def __init__(
        self,
        employee_id: str,
        script_path: str = "",
        timeout_seconds: int = 3600,
    ) -> None:
        self.employee_id = employee_id
        self.script_path = script_path or str(EMPLOYEES_DIR / employee_id / LAUNCH_SH_FILENAME)
        self.timeout_seconds = timeout_seconds
        self._process: asyncio.subprocess.Process | None = None

    async def execute(
        self,
        task_description: str,
        context: TaskContext,
        on_log: Callable[[str, str], None] | None = None,
    ) -> LaunchResult:
        # Write prompt to temp file to avoid env var length limits.
        # launch.sh reads from OMC_TASK_DESCRIPTION_FILE.
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="omc_prompt_",
            delete=False, encoding="utf-8",
        )
        prompt_file.write(task_description)
        prompt_file.close()

        try:
            return await self._run_subprocess(task_description, prompt_file.name, context, on_log)
        finally:
            try:
                os.unlink(prompt_file.name)
            except OSError as exc:
                logger.debug("Failed to remove prompt file {}: {}", prompt_file.name, exc)

    async def _run_subprocess(
        self,
        task_description: str,
        prompt_path: str,
        context: TaskContext,
        on_log: Callable[[str, str], None] | None,
    ) -> LaunchResult:
        env = {
            **os.environ,
            "OMC_EMPLOYEE_ID": context.employee_id,
            "OMC_TASK_ID": context.task_id,
            "OMC_PROJECT_ID": context.project_id,
            "OMC_PROJECT_DIR": context.work_dir,
            "OMC_TASK_DESCRIPTION_FILE": prompt_path,
            # Keep OMC_TASK_DESCRIPTION for backward compat with old launch.sh scripts
            "OMC_TASK_DESCRIPTION": task_description,
            "OMC_SERVER_URL": f"http://localhost:{os.environ.get('OMC_PORT', '8000')}",
        }

        cwd = context.work_dir or str(EMPLOYEES_DIR / self.employee_id)

        self._process = await asyncio.create_subprocess_exec(
            "bash", self.script_path, str(EMPLOYEES_DIR / self.employee_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        if on_log:
            on_log("start", f"Started subprocess PID={self._process.pid}")

        try:
            stdout, stderr = await asyncio.wait_for(
                self._process.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Task timeout after {}s for employee {} (PID={})",
                self.timeout_seconds, self.employee_id, self._process.pid,
            )
            await self.cancel()
            raise TimeoutError(f"Timeout after {self.timeout_seconds}s") from None

        if on_log and stderr:
            on_log("stderr", stderr.decode(errors="replace")[:2000])

        if self._process.returncode != 0:
            err_msg = stderr.decode(errors="replace")[:500] if stderr else "Unknown error"
            if on_log:
                on_log("error", f"Exit code {self._process.returncode}: {err_msg}")
            return LaunchResult(output=f"Error (exit {self._process.returncode}): {err_msg}")

        raw = stdout.decode(errors="replace").strip()
        try:
            data = json.loads(raw)
            return LaunchResult(
                output=data.get("output", raw),
                model_used=data.get("model", ""),
                input_tokens=data.get("input_tokens", 0),
                output_tokens=data.get("output_tokens", 0),
                total_tokens=data.get("input_tokens", 0) + data.get("output_tokens", 0),
            )
        except (json.JSONDecodeError, AttributeError):
            return LaunchResult(output=raw)

    async def cancel(self) -> None:
        """Two-stage kill: SIGTERM -> poll every 5s -> SIGKILL after 30s."""
        proc = self._process
        if proc is None or proc.returncode is not None:
            return

        logger.info("Cancelling subprocess PID={} for {}", proc.pid, self.employee_id)
        proc.terminate()

        elapsed = 0
        while elapsed < _KILL_GRACE_PERIOD:
            try:
                await asyncio.wait_for(proc.wait(), timeout=_KILL_POLL_INTERVAL)
                logger.info("Process PID={} exited gracefully after {}s", proc.pid, elapsed)
                return
            except asyncio.TimeoutError:
                elapsed += _KILL_POLL_INTERVAL
                logger.debug("Process PID={} still alive after {}s", proc.pid, elapsed)

        logger.warning("Process PID={} did not exit after {}s — sending SIGKILL", proc.pid, _KILL_GRACE_PERIOD)
        proc.kill()
        await proc.wait()
