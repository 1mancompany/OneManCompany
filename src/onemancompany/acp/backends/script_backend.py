"""Script ACP Backend — wraps bash script execution for ACP subprocess tasks.

Mirrors the logic from :class:`onemancompany.core.subprocess_executor.SubprocessExecutor`
but conforms to the ACP backend interface (``execute`` / ``set_model`` /
``set_config``) instead of the Launcher protocol.

The script receives the task description via a temp file path stored in the
``OMC_TASK_DESCRIPTION_FILE`` env var so that very long prompts (with history)
don't exceed OS env-var length limits (~128 KB on macOS).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any

from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR

_LAUNCH_SH = "launch.sh"
_TIMEOUT_SECONDS = 3600


class ScriptAcpBackend:
    """ACP backend that executes tasks by spawning ``launch.sh`` as a bash subprocess.

    The subprocess is expected to print either:
      - A JSON object to stdout:
        ``{"output": "...", "model": "...", "input_tokens": N, "output_tokens": N}``
      - Or plain text, which is used as-is for ``output``.

    Exit code != 0 is treated as an error.
    """

    #: Identifies this backend for ACP agent_process.py dispatch.
    executor_type: str = "script"

    def __init__(self, employee_id: str, server_url: str) -> None:
        self._employee_id = employee_id
        self._server_url = server_url.rstrip("/")
        self._script_path = str(EMPLOYEES_DIR / employee_id / _LAUNCH_SH)

        logger.debug(
            "ScriptAcpBackend: constructed employee_id={eid} script={script}",
            eid=employee_id,
            script=self._script_path,
        )

    # ------------------------------------------------------------------
    # ACP Backend API
    # ------------------------------------------------------------------

    async def execute(
        self,
        task_description: str,
        client: Any,
        session_id: str,
        cancel_event: asyncio.Event,
    ) -> dict:
        """Spawn ``launch.sh`` with the task and return a result dict.

        Args:
            task_description: Task prompt to deliver to the script.
            client: ACP AgentSideConnection (unused by this backend).
            session_id: ACP session identifier (unused by this backend).
            cancel_event: Set to abort — not yet propagated to subprocess.

        Returns:
            Dict with keys: ``output`` (str), ``error`` (str|None),
            ``model`` (str), ``tokens`` (dict with ``input``, ``output``,
            ``cost_usd``).
        """
        logger.debug(
            "ScriptAcpBackend.execute: employee={eid} script={script} task={t:.80}",
            eid=self._employee_id,
            script=self._script_path,
            t=task_description,
        )

        # Write prompt to temp file — avoids OS env-var length limits
        prompt_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            prefix="omc_prompt_",
            delete=False,
            encoding="utf-8",
        )
        prompt_file.write(task_description)
        prompt_file.close()

        try:
            return await self._run_subprocess(task_description, prompt_file.name)
        finally:
            try:
                os.unlink(prompt_file.name)
            except OSError as exc:
                logger.debug(
                    "ScriptAcpBackend: failed to remove prompt file {}: {}",
                    prompt_file.name,
                    exc,
                )

    async def _run_subprocess(self, task_description: str, prompt_path: str) -> dict:
        env = {
            **os.environ,
            "OMC_EMPLOYEE_ID": self._employee_id,
            "OMC_TASK_DESCRIPTION_FILE": prompt_path,
            # Backward compat — some scripts read OMC_TASK_DESCRIPTION directly
            "OMC_TASK_DESCRIPTION": task_description,
            "OMC_SERVER_URL": self._server_url,
        }

        cwd = str(EMPLOYEES_DIR / self._employee_id)

        logger.info(
            "ScriptAcpBackend: spawning bash {} cwd={}",
            self._script_path,
            cwd,
        )

        proc = await asyncio.create_subprocess_exec(
            "bash",
            self._script_path,
            cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "ScriptAcpBackend: timeout after {}s for employee {} (PID={})",
                _TIMEOUT_SECONDS,
                self._employee_id,
                proc.pid,
            )
            try:
                proc.terminate()
            except ProcessLookupError:
                logger.debug("ScriptAcpBackend: process already exited before terminate() for employee={}", self._employee_id)
            return _error_result(f"[script timeout] {_TIMEOUT_SECONDS}s exceeded")

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace")[:500] if stderr else "Unknown error"
            error = f"Error (exit {proc.returncode}): {err_msg}"
            logger.warning(
                "ScriptAcpBackend: non-zero exit {} for employee {}: {}",
                proc.returncode,
                self._employee_id,
                err_msg[:200],
            )
            return _error_result(error)

        raw = (stdout or b"").decode(errors="replace").strip()

        try:
            data = json.loads(raw)
            output = data.get("output", raw)
            model = data.get("model", "")
            input_tokens = data.get("input_tokens", 0)
            output_tokens = data.get("output_tokens", 0)
        except (json.JSONDecodeError, AttributeError):
            output = raw
            model = ""
            input_tokens = 0
            output_tokens = 0

        logger.debug(
            "ScriptAcpBackend.execute: done employee={eid} model={model} "
            "input_tokens={it} output_tokens={ot}",
            eid=self._employee_id,
            model=model,
            it=input_tokens,
            ot=output_tokens,
        )

        return {
            "output": output,
            "error": None,
            "model": model,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "cost_usd": None,
            },
        }

    def set_model(self, model_id: str) -> None:
        """No-op — model selection is managed within the launch.sh script."""
        logger.debug(
            "ScriptAcpBackend.set_model: ignored (script manages own model) model_id={mid}",
            mid=model_id,
        )

    def set_config(self, key: str, value: Any) -> None:
        """No-op — configuration is embedded in the script itself."""
        logger.debug(
            "ScriptAcpBackend.set_config: ignored key={k} value={v}",
            k=key,
            v=value,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_result(error: str) -> dict:
    return {
        "output": "",
        "error": error,
        "model": "",
        "tokens": {"input": 0, "output": 0, "cost_usd": None},
    }
