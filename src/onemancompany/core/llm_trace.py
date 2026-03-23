"""LLM interaction trace logger.

Two trace formats:
  1. Per-event JSONL (legacy LlmTracer) — one line per prompt/response/tool_call
  2. SFT JSONL — one line per complete conversation, suitable for fine-tuning

SFT records are written to {project_dir}/sft_trace.jsonl with full messages,
tool schemas, model info, and token usage.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from onemancompany.core.config import ENCODING_UTF8

SFT_TRACE_FILENAME = "sft_trace.jsonl"


# ---------------------------------------------------------------------------
# Legacy per-event tracer (unchanged)
# ---------------------------------------------------------------------------

class LlmTracer:
    """Append-only JSONL logger for LLM interactions."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _append(self, record: dict) -> None:
        record["ts"] = datetime.now().isoformat()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding=ENCODING_UTF8) as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_prompt(self, node_id: str, employee_id: str, content: str) -> None:
        self._append({"node_id": node_id, "employee_id": employee_id, "type": "prompt", "content": content})

    def log_response(
        self, node_id: str, employee_id: str, content: str,
        *, model: str = "", input_tokens: int = 0, output_tokens: int = 0,
    ) -> None:
        self._append({
            "node_id": node_id, "employee_id": employee_id, "type": "response",
            "content": content, "model": model,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
        })

    def log_tool_call(self, node_id: str, employee_id: str, tool_name: str, args: dict) -> None:
        self._append({
            "node_id": node_id, "employee_id": employee_id, "type": "tool_call",
            "content": {"tool": tool_name, "args": args},
        })

    def log_tool_result(self, node_id: str, employee_id: str, result: dict) -> None:
        self._append({
            "node_id": node_id, "employee_id": employee_id, "type": "tool_result",
            "content": result,
        })


# ---------------------------------------------------------------------------
# SFT trace — full conversation records for fine-tuning
# ---------------------------------------------------------------------------

def _serialize_message(msg) -> dict[str, Any]:
    """Convert a LangChain message object to an SFT-compatible dict.

    Handles SystemMessage, HumanMessage, AIMessage (with tool_calls),
    and ToolMessage.
    """
    from langchain_core.messages import (
        AIMessage, HumanMessage, SystemMessage, ToolMessage,
    )

    if isinstance(msg, SystemMessage):
        return {"role": "system", "content": msg.content}
    elif isinstance(msg, HumanMessage):
        return {"role": "user", "content": msg.content}
    elif isinstance(msg, AIMessage):
        entry: dict[str, Any] = {"role": "assistant"}
        # Content can be str or list of blocks
        if isinstance(msg.content, str):
            entry["content"] = msg.content
        elif isinstance(msg.content, list):
            # Multi-block content (text + tool_use mixed)
            entry["content"] = msg.content
        else:
            entry["content"] = str(msg.content)
        # Tool calls
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ]
        return entry
    elif isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": getattr(msg, "tool_call_id", ""),
            "content": msg.content if isinstance(msg.content, str) else str(msg.content),
        }
    else:
        # Fallback for unknown message types
        return {
            "role": getattr(msg, "type", "unknown"),
            "content": str(getattr(msg, "content", "")),
        }


def _serialize_tool_schema(tool) -> dict[str, Any]:
    """Convert a LangChain StructuredTool to an OpenAI-compatible tool schema."""
    schema = {}
    if hasattr(tool, "args_schema") and tool.args_schema:
        schema = tool.args_schema.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", ""),
            "parameters": schema,
        },
    }


def write_sft_record(
    project_dir: str,
    *,
    employee_id: str,
    node_id: str = "",
    source: str = "langchain",
    messages: list | None = None,
    tools: list | None = None,
    model: str = "",
    usage: dict | None = None,
) -> None:
    """Write one complete conversation as an SFT training record.

    Args:
        project_dir: Project workspace directory (sft_trace.jsonl lives here).
        employee_id: The employee who ran this conversation.
        node_id: Task node ID (for traceability).
        source: "langchain" | "daemon" | "tracked_ainvoke".
        messages: List of LangChain message objects or pre-serialized dicts.
        tools: List of LangChain StructuredTool objects or pre-serialized dicts.
        model: Model name/ID used.
        usage: Token usage dict {input_tokens, output_tokens, ...}.
    """
    if not project_dir or not messages:
        return

    path = Path(project_dir) / SFT_TRACE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize messages
    serialized_msgs = []
    for msg in messages:
        if isinstance(msg, dict):
            serialized_msgs.append(msg)
        elif isinstance(msg, str):
            serialized_msgs.append({"role": "user", "content": msg})
        else:
            serialized_msgs.append(_serialize_message(msg))

    # Serialize tools
    serialized_tools = []
    if tools:
        for t in tools:
            if isinstance(t, dict):
                serialized_tools.append(t)
            else:
                try:
                    serialized_tools.append(_serialize_tool_schema(t))
                except Exception as e:
                    logger.debug("[sft_trace] failed to serialize tool {}: {}", getattr(t, "name", "?"), e)

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "employee_id": employee_id,
        "node_id": node_id,
        "source": source,
        "model": model,
        "messages": serialized_msgs,
    }
    if serialized_tools:
        record["tools"] = serialized_tools
    if usage:
        record["usage"] = usage

    try:
        with path.open("a", encoding=ENCODING_UTF8) as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("[sft_trace] write failed: {}", e)
