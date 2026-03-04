"""3-tier safe execution — agents never crash the system.

Tier 1: Normal execution
Tier 2: Simplified retry (truncated description, shorter timeout)
Tier 3: Structured failure (always returns a result, never raises)
"""

from __future__ import annotations

import asyncio

from onemancompany.core.errors import ErrorCode, StructuredError, classify_exception
from onemancompany.core.models import AgentResult


async def safe_agent_execute(
    execute_fn,
    task_description: str,
    *,
    timeout: float = 120.0,
) -> tuple[AgentResult, list[StructuredError]]:
    """Execute an agent task with 3-level fallback.

    Parameters
    ----------
    execute_fn : async callable(str) -> str
        The actual execution function (e.g., agent.run or launcher.execute).
        Takes a task description string and returns the output string.
    task_description : str
        The task to execute.
    timeout : float
        Timeout in seconds for Tier 1. Tier 2 uses half this value.

    Returns
    -------
    tuple[AgentResult, list[StructuredError]]
        Always returns a result (never raises). Diagnostics list all errors encountered.
    """
    diagnostics: list[StructuredError] = []

    # Tier 1: Normal execution
    try:
        output = await asyncio.wait_for(
            execute_fn(task_description),
            timeout=timeout,
        )
        return AgentResult(success=True, output=output or "", attempt=1), diagnostics
    except Exception as e1:
        err = classify_exception(e1)
        diagnostics.append(err)
        if not err.recoverable:
            return AgentResult(
                success=False, output="", error=err.message, attempt=1,
            ), diagnostics

    # Tier 2: Simplified retry
    try:
        simplified = f"[Simplified] {task_description[:500]}"
        output = await asyncio.wait_for(
            execute_fn(simplified),
            timeout=timeout / 2,
        )
        diagnostics.append(StructuredError(
            code=ErrorCode.AGENT_TOOL_FAILURE,
            severity="warning",
            message="Tier 1 failed, Tier 2 simplified execution succeeded",
            suggestion="Original task may be too complex",
        ))
        return AgentResult(
            success=True, output=output or "", attempt=2,
        ), diagnostics
    except Exception as e2:
        diagnostics.append(classify_exception(e2))

    # Tier 3: Structured failure (never raises)
    return AgentResult(
        success=False,
        output="",
        error=f"All 3 tiers failed: {diagnostics[-1].message}",
        attempt=3,
    ), diagnostics
