"""Tests for LLM interaction trace logger."""
from __future__ import annotations

import json
from pathlib import Path

from onemancompany.core.llm_trace import LlmTracer


class TestLlmTracer:
    def test_log_prompt(self, tmp_path):
        path = tmp_path / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_prompt("node1", "00003", "You are a COO...")

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["node_id"] == "node1"
        assert record["employee_id"] == "00003"
        assert record["type"] == "prompt"
        assert record["content"] == "You are a COO..."
        assert "ts" in record

    def test_log_response(self, tmp_path):
        path = tmp_path / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_response("node1", "00003", "I'll dispatch...", model="claude-sonnet-4-6", input_tokens=100, output_tokens=50)

        record = json.loads(path.read_text().strip())
        assert record["type"] == "response"
        assert record["model"] == "claude-sonnet-4-6"
        assert record["input_tokens"] == 100
        assert record["output_tokens"] == 50

    def test_log_tool_call(self, tmp_path):
        path = tmp_path / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_tool_call("node1", "00003", "dispatch_child", {"employee_id": "00010", "description": "Build API"})

        record = json.loads(path.read_text().strip())
        assert record["type"] == "tool_call"
        assert record["content"]["tool"] == "dispatch_child"
        assert record["content"]["args"]["employee_id"] == "00010"

    def test_log_tool_result(self, tmp_path):
        path = tmp_path / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_tool_result("node1", "00003", {"status": "ok", "node_id": "child1"})

        record = json.loads(path.read_text().strip())
        assert record["type"] == "tool_result"
        assert record["content"]["status"] == "ok"

    def test_multiple_entries_appended(self, tmp_path):
        path = tmp_path / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_prompt("n1", "00003", "prompt1")
        tracer.log_response("n1", "00003", "response1")
        tracer.log_prompt("n2", "00010", "prompt2")

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "llm_trace.jsonl"
        tracer = LlmTracer(path)
        tracer.log_prompt("n1", "00003", "test")
        assert path.exists()
