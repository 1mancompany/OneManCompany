"""Structured error types — replaces catch-all ``except Exception`` patterns.

Provides error classification so the frontend can show actionable messages
and the system can decide whether to retry, escalate, or abort.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class ErrorCode(str, Enum):
    # Agent execution
    AGENT_TIMEOUT = "agent_timeout"
    AGENT_RECURSION_LIMIT = "agent_recursion_limit"
    AGENT_TOOL_FAILURE = "agent_tool_failure"
    AGENT_EMPTY_RESPONSE = "agent_empty_response"

    # LLM provider
    LLM_RATE_LIMIT = "llm_rate_limit"
    LLM_QUOTA_EXCEEDED = "llm_quota_exceeded"
    LLM_AUTH_FAILURE = "llm_auth_failure"
    LLM_CONTEXT_OVERFLOW = "llm_context_overflow"
    LLM_PROVIDER_DOWN = "llm_provider_down"

    # Business logic
    MEETING_ROOM_UNAVAILABLE = "meeting_room_unavailable"
    EMPLOYEE_NOT_FOUND = "employee_not_found"
    BUDGET_EXCEEDED = "budget_exceeded"
    PERMISSION_DENIED = "permission_denied"
    CONTRACT_VIOLATION = "contract_violation"

    # System
    FILE_IO_ERROR = "file_io_error"
    STATE_CORRUPTION = "state_corruption"
    WEBSOCKET_ERROR = "websocket_error"


class StructuredError(BaseModel):
    """A classified error with actionable fix suggestion."""
    code: ErrorCode
    severity: Literal["warning", "error", "critical"]
    message: str
    suggestion: str
    context: dict = {}
    recoverable: bool = True


def classify_exception(exc: Exception) -> StructuredError:
    """Infer a structured error from an exception type + message.

    Covers common failure modes: timeout, recursion, rate limit, auth, IO.
    Falls back to AGENT_TOOL_FAILURE for unrecognized exceptions.
    """
    msg = str(exc).lower()
    exc_type = type(exc).__name__

    if isinstance(exc, asyncio.TimeoutError):
        return StructuredError(
            code=ErrorCode.AGENT_TIMEOUT,
            severity="error",
            message=f"Agent execution timed out: {exc}",
            suggestion="Consider simplifying the task or increasing timeout",
            recoverable=True,
        )

    if "GraphRecursionError" in exc_type or "recursion" in msg:
        return StructuredError(
            code=ErrorCode.AGENT_RECURSION_LIMIT,
            severity="error",
            message=f"Agent hit recursion limit: {exc}",
            suggestion="Task may be too complex; consider splitting into subtasks",
            recoverable=True,
        )

    if ("insufficient" in msg and ("fund" in msg or "credit" in msg or "balance" in msg)
            or "402" in msg or "quota" in msg and "exceed" in msg
            or "billing" in msg and ("limit" in msg or "exceed" in msg)):
        return StructuredError(
            code=ErrorCode.LLM_QUOTA_EXCEEDED,
            severity="error",
            message=f"LLM quota/billing exceeded: {exc}",
            suggestion="Check account balance or billing settings",
            recoverable=True,
        )

    if "rate_limit" in msg or "429" in msg or "too many requests" in msg:
        return StructuredError(
            code=ErrorCode.LLM_RATE_LIMIT,
            severity="warning",
            message=f"LLM rate limit exceeded: {exc}",
            suggestion="Will auto-retry after 60s cooldown",
            recoverable=True,
        )

    if "auth" in msg or "401" in msg or "403" in msg or "unauthorized" in msg:
        return StructuredError(
            code=ErrorCode.LLM_AUTH_FAILURE,
            severity="critical",
            message=f"LLM authentication failed: {exc}",
            suggestion="Check API key configuration in employee profile",
            recoverable=False,
        )

    if "context" in msg and ("length" in msg or "overflow" in msg or "too long" in msg):
        return StructuredError(
            code=ErrorCode.LLM_CONTEXT_OVERFLOW,
            severity="error",
            message=f"LLM context window exceeded: {exc}",
            suggestion="Reduce task description length or conversation history",
            recoverable=True,
        )

    if isinstance(exc, (OSError, IOError)):
        return StructuredError(
            code=ErrorCode.FILE_IO_ERROR,
            severity="error",
            message=f"File I/O error: {exc}",
            suggestion="Check file permissions and disk space",
            context={"exception_type": exc_type},
            recoverable=True,
        )

    if "connection" in msg or "502" in msg or "503" in msg or "504" in msg:
        return StructuredError(
            code=ErrorCode.LLM_PROVIDER_DOWN,
            severity="error",
            message=f"LLM provider unavailable: {exc}",
            suggestion="Provider may be down; will retry automatically",
            recoverable=True,
        )

    # Fallback
    return StructuredError(
        code=ErrorCode.AGENT_TOOL_FAILURE,
        severity="error",
        message=f"Unclassified error: {exc}",
        suggestion="Check logs for details",
        context={"exception_type": exc_type},
        recoverable=True,
    )
