"""LLM interaction trace logger.

Appends one JSON line per interaction to {project_dir}/llm_trace.jsonl.
Records prompts, responses, tool calls, and tool results for each TaskNode.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from onemancompany.core.config import ENCODING_UTF8


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
