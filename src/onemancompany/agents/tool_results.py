"""Typed tool return values — replaces bare {"status": "ok/error"} dicts.

Provides structured return types for LangChain tool functions,
enabling type-safe tool result handling.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel


class ToolSuccess(BaseModel):
    """Successful tool execution."""
    status: Literal["ok"] = "ok"
    message: str = ""


class ToolError(BaseModel):
    """Failed tool execution with diagnostic info."""
    status: Literal["error"] = "error"
    message: str
    code: str = "unknown"
    suggestion: str = ""


# Specialized success types

class ReadFileResult(ToolSuccess):
    path: str
    content: str
    size: int = 0


class ListDirectoryResult(ToolSuccess):
    path: str
    entries: list[str] = []


class SaveFileResult(ToolSuccess):
    path: str
    bytes_written: int = 0


class DispatchTaskResult(ToolSuccess):
    task_id: str = ""
    assigned_to: str = ""
    employee_name: str = ""


class MeetingResult(ToolSuccess):
    meeting_id: str = ""
    room_id: str = ""
    participants: list[str] = []


# Union for type checking
ToolResult = Union[ToolSuccess, ToolError]
