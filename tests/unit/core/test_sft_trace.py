"""Tests for SFT (Supervised Fine-Tuning) trace functionality."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.llm_trace import (
    DEBUG_TRACE_FILENAME,
    _serialize_message,
    _serialize_tool_schema,
    write_debug_trace,
    write_debug_trace_async,
)


class TestSerializeMessage:
    """Test _serialize_message for all LangChain message types."""

    def test_system_message(self):
        from langchain_core.messages import SystemMessage
        msg = SystemMessage(content="You are a helpful assistant.")
        result = _serialize_message(msg)
        assert result == {"role": "system", "content": "You are a helpful assistant."}

    def test_human_message(self):
        from langchain_core.messages import HumanMessage
        msg = HumanMessage(content="Hello world")
        result = _serialize_message(msg)
        assert result == {"role": "user", "content": "Hello world"}

    def test_ai_message_text(self):
        from langchain_core.messages import AIMessage
        msg = AIMessage(content="I can help with that.")
        result = _serialize_message(msg)
        assert result["role"] == "assistant"
        assert result["content"] == "I can help with that."
        assert "tool_calls" not in result

    def test_ai_message_with_tool_calls(self):
        from langchain_core.messages import AIMessage
        msg = AIMessage(
            content="Let me check that.",
            tool_calls=[
                {"id": "tc_1", "name": "list_files", "args": {"path": "/workspace"}},
            ],
        )
        result = _serialize_message(msg)
        assert result["role"] == "assistant"
        assert result["content"] == "Let me check that."
        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc["id"] == "tc_1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "list_files"
        assert json.loads(tc["function"]["arguments"]) == {"path": "/workspace"}

    def test_ai_message_list_content(self):
        from langchain_core.messages import AIMessage
        msg = AIMessage(content=[
            {"type": "text", "text": "Some text"},
            {"type": "tool_use", "id": "tc_1", "name": "foo", "input": {}},
        ])
        result = _serialize_message(msg)
        assert result["role"] == "assistant"
        assert isinstance(result["content"], list)

    def test_tool_message(self):
        from langchain_core.messages import ToolMessage
        msg = ToolMessage(content="File not found", tool_call_id="tc_1")
        result = _serialize_message(msg)
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "tc_1"
        assert result["content"] == "File not found"

    def test_unknown_message_type(self):
        """Unknown message types should produce a fallback dict."""
        class CustomMsg:
            type = "custom"
            content = "custom content"
        result = _serialize_message(CustomMsg())
        assert result["role"] == "custom"
        assert result["content"] == "custom content"


class TestSerializeToolSchema:
    """Test _serialize_tool_schema for LangChain StructuredTool."""

    def test_basic_tool(self):
        tool = MagicMock()
        tool.name = "read_file"
        tool.description = "Read a file from disk"
        tool.args_schema = None
        result = _serialize_tool_schema(tool)
        assert result["type"] == "function"
        assert result["function"]["name"] == "read_file"
        assert result["function"]["description"] == "Read a file from disk"
        assert result["function"]["parameters"] == {}

    def test_tool_with_schema(self):
        from pydantic import BaseModel, Field

        class ReadFileArgs(BaseModel):
            path: str = Field(description="File path to read")

        tool = MagicMock()
        tool.name = "read_file"
        tool.description = "Read a file"
        tool.args_schema = ReadFileArgs
        result = _serialize_tool_schema(tool)
        params = result["function"]["parameters"]
        assert "properties" in params
        assert "path" in params["properties"]


class TestWriteSftRecord:
    """Test write_debug_trace output format and behavior."""

    def test_writes_jsonl(self, tmp_path):
        from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
        messages = [
            SystemMessage(content="You are a COO."),
            HumanMessage(content="Plan the sprint"),
            AIMessage(content="Here is the plan."),
        ]
        write_debug_trace(
            str(tmp_path),
            employee_id="00003",
            node_id="node_abc",
            source="langchain",
            messages=messages,
            model="claude-sonnet-4-6",
            usage={"input_tokens": 500, "output_tokens": 200},
        )

        sft_path = tmp_path / DEBUG_TRACE_FILENAME
        assert sft_path.exists()

        record = json.loads(sft_path.read_text().strip())
        assert record["employee_id"] == "00003"
        assert record["node_id"] == "node_abc"
        assert record["source"] == "langchain"
        assert record["model"] == "claude-sonnet-4-6"
        assert len(record["messages"]) == 3
        assert record["messages"][0]["role"] == "system"
        assert record["messages"][1]["role"] == "user"
        assert record["messages"][2]["role"] == "assistant"
        assert record["usage"]["input_tokens"] == 500
        assert "ts" in record

    def test_with_tool_calls(self, tmp_path):
        from langchain_core.messages import (
            SystemMessage, HumanMessage, AIMessage, ToolMessage,
        )
        messages = [
            SystemMessage(content="You are an agent."),
            HumanMessage(content="List files"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc_1", "name": "ls", "args": {"path": "/"}}],
            ),
            ToolMessage(content="file1.txt\nfile2.txt", tool_call_id="tc_1"),
            AIMessage(content="Found 2 files."),
        ]
        write_debug_trace(
            str(tmp_path),
            employee_id="00010",
            source="langchain",
            messages=messages,
            model="test-model",
        )

        record = json.loads((tmp_path / DEBUG_TRACE_FILENAME).read_text().strip())
        assert len(record["messages"]) == 5
        # Check tool call format
        ai_msg = record["messages"][2]
        assert ai_msg["role"] == "assistant"
        assert len(ai_msg["tool_calls"]) == 1
        assert ai_msg["tool_calls"][0]["function"]["name"] == "ls"
        # Check tool result
        tool_msg = record["messages"][3]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "tc_1"

    def test_with_tools_schema(self, tmp_path):
        from langchain_core.messages import HumanMessage, AIMessage
        tool = MagicMock()
        tool.name = "dispatch_child"
        tool.description = "Dispatch a subtask"
        tool.args_schema = None

        write_debug_trace(
            str(tmp_path),
            employee_id="00003",
            source="langchain",
            messages=[HumanMessage(content="Do something"), AIMessage(content="OK")],
            tools=[tool],
        )

        record = json.loads((tmp_path / DEBUG_TRACE_FILENAME).read_text().strip())
        assert "tools" in record
        assert len(record["tools"]) == 1
        assert record["tools"][0]["function"]["name"] == "dispatch_child"

    def test_pre_serialized_dicts(self, tmp_path):
        """Pre-serialized dict messages should pass through as-is."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        write_debug_trace(
            str(tmp_path),
            employee_id="00003",
            source="daemon",
            messages=messages,
        )

        record = json.loads((tmp_path / DEBUG_TRACE_FILENAME).read_text().strip())
        assert record["messages"][0] == {"role": "user", "content": "Hello"}

    def test_string_message_converted(self, tmp_path):
        """Plain string messages should be converted to user role."""
        write_debug_trace(
            str(tmp_path),
            employee_id="00003",
            source="tracked_ainvoke",
            messages=["What is 2+2?"],
        )
        record = json.loads((tmp_path / DEBUG_TRACE_FILENAME).read_text().strip())
        assert record["messages"][0] == {"role": "user", "content": "What is 2+2?"}

    def test_skips_empty_messages(self, tmp_path):
        """Should not write if messages is empty or project_dir is empty."""
        write_debug_trace("", employee_id="00003", messages=[{"role": "user", "content": "hi"}])
        write_debug_trace(str(tmp_path), employee_id="00003", messages=[])
        write_debug_trace(str(tmp_path), employee_id="00003", messages=None)
        assert not (tmp_path / DEBUG_TRACE_FILENAME).exists()

    def test_appends_multiple_records(self, tmp_path):
        from langchain_core.messages import HumanMessage, AIMessage
        for i in range(3):
            write_debug_trace(
                str(tmp_path),
                employee_id="00003",
                source="langchain",
                messages=[HumanMessage(content=f"msg {i}"), AIMessage(content=f"resp {i}")],
            )

        lines = (tmp_path / DEBUG_TRACE_FILENAME).read_text().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines):
            record = json.loads(line)
            assert record["messages"][0]["content"] == f"msg {i}"

    def test_no_tools_key_when_empty(self, tmp_path):
        from langchain_core.messages import HumanMessage, AIMessage
        write_debug_trace(
            str(tmp_path),
            employee_id="00003",
            source="langchain",
            messages=[HumanMessage(content="hi"), AIMessage(content="hello")],
        )
        record = json.loads((tmp_path / DEBUG_TRACE_FILENAME).read_text().strip())
        assert "tools" not in record

    def test_dict_tool_passes_through(self, tmp_path):
        """Line 182: pre-serialized dict tools pass through as-is."""
        from langchain_core.messages import HumanMessage, AIMessage
        dict_tool = {"type": "function", "function": {"name": "my_tool"}}
        write_debug_trace(
            str(tmp_path),
            employee_id="00003",
            source="langchain",
            messages=[HumanMessage(content="hi"), AIMessage(content="ok")],
            tools=[dict_tool],
        )
        record = json.loads((tmp_path / DEBUG_TRACE_FILENAME).read_text().strip())
        assert record["tools"][0] == dict_tool

    def test_tool_serialization_exception_skips(self, tmp_path):
        """Lines 186-187: failed tool serialization is skipped gracefully."""
        from langchain_core.messages import HumanMessage, AIMessage
        bad_tool = MagicMock()
        bad_tool.name = "broken"
        # Make args_schema.model_json_schema raise
        bad_tool.args_schema = MagicMock()
        bad_tool.args_schema.model_json_schema.side_effect = Exception("schema broken")
        # Also patch _serialize_tool_schema to raise
        with patch("onemancompany.core.llm_trace._serialize_tool_schema", side_effect=Exception("broken")):
            write_debug_trace(
                str(tmp_path),
                employee_id="00003",
                source="langchain",
                messages=[HumanMessage(content="hi"), AIMessage(content="ok")],
                tools=[bad_tool],
            )
        record = json.loads((tmp_path / DEBUG_TRACE_FILENAME).read_text().strip())
        # Tool serialization failed, so no tools key (empty list not written)
        assert "tools" not in record

    def test_write_oserror_handled(self, tmp_path):
        """Line 207: OSError on write is handled gracefully."""
        from langchain_core.messages import HumanMessage, AIMessage
        # Use a non-writable directory
        with patch("pathlib.Path.open", side_effect=OSError("permission denied")):
            # Should not raise
            write_debug_trace(
                str(tmp_path),
                employee_id="00003",
                source="langchain",
                messages=[HumanMessage(content="hi"), AIMessage(content="ok")],
            )


class TestDaemonSftAccumulation:
    """Test ClaudeDaemon._accumulate_debug_assistant."""

    def test_text_only(self):
        from onemancompany.core.claude_session import ClaudeDaemon
        debug_messages = []
        message = {
            "content": [{"type": "text", "text": "Hello world"}],
        }
        ClaudeDaemon._accumulate_debug_assistant(debug_messages, message)
        assert len(debug_messages) == 1
        assert debug_messages[0]["role"] == "assistant"
        assert debug_messages[0]["content"] == "Hello world"
        assert "tool_calls" not in debug_messages[0]

    def test_tool_use(self):
        from onemancompany.core.claude_session import ClaudeDaemon
        debug_messages = []
        message = {
            "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "tc_1", "name": "ls", "input": {"path": "/"}},
            ],
        }
        ClaudeDaemon._accumulate_debug_assistant(debug_messages, message)
        assert len(debug_messages) == 1
        entry = debug_messages[0]
        assert entry["role"] == "assistant"
        assert entry["content"] == "Let me check."
        assert len(entry["tool_calls"]) == 1
        assert entry["tool_calls"][0]["function"]["name"] == "ls"

    def test_tool_result(self):
        from onemancompany.core.claude_session import ClaudeDaemon
        debug_messages = []
        message = {
            "content": [
                {"type": "tool_result", "tool_use_id": "tc_1", "content": "file.txt"},
            ],
        }
        ClaudeDaemon._accumulate_debug_assistant(debug_messages, message)
        # Tool result only — no spurious empty assistant entry
        assert len(debug_messages) == 1
        assert debug_messages[0]["role"] == "tool"
        assert debug_messages[0]["tool_call_id"] == "tc_1"
        assert debug_messages[0]["content"] == "file.txt"

    def test_empty_content(self):
        from onemancompany.core.claude_session import ClaudeDaemon
        debug_messages = []
        message = {"content": []}
        ClaudeDaemon._accumulate_debug_assistant(debug_messages, message)
        # Empty content — no entry appended
        assert len(debug_messages) == 0


class TestWriteSftRecordAsync:
    """Test the non-blocking write_debug_trace_async wrapper."""

    @pytest.mark.asyncio
    async def test_async_write_produces_file(self, tmp_path):
        """Async write should eventually produce the same JSONL output."""
        import asyncio
        write_debug_trace_async(
            str(tmp_path),
            employee_id="00003",
            source="langchain",
            messages=[{"role": "user", "content": "hello"}],
            model="test",
        )
        # Give the executor a moment to flush
        await asyncio.sleep(0.1)
        sft_path = tmp_path / DEBUG_TRACE_FILENAME
        assert sft_path.exists()
        record = json.loads(sft_path.read_text().strip())
        assert record["employee_id"] == "00003"
        assert record["messages"][0]["content"] == "hello"

    def test_sync_fallback_no_loop(self, tmp_path):
        """Without a running event loop, falls back to synchronous write."""
        write_debug_trace_async(
            str(tmp_path),
            employee_id="00003",
            source="langchain",
            messages=[{"role": "user", "content": "sync fallback"}],
        )
        sft_path = tmp_path / DEBUG_TRACE_FILENAME
        assert sft_path.exists()
        record = json.loads(sft_path.read_text().strip())
        assert record["messages"][0]["content"] == "sync fallback"
