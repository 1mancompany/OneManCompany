"""Tests for vessel.py — dict content in _log_node and project_id in _publish_log_event."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAppendNodeExecutionLogDict:
    """_append_node_execution_log should handle dict content by extracting string."""

    def test_dict_content_writes_string_to_jsonl(self, tmp_path):
        """When content is a dict, JSONL should contain content['content'] as string."""
        from onemancompany.core.vessel import _append_node_execution_log

        project_dir = str(tmp_path)
        node_id = "node_001"

        dict_content = {
            "tool_name": "list_colleagues",
            "tool_args": {"department": "eng"},
            "content": "list_colleagues({'department': 'eng'})",
        }
        _append_node_execution_log(project_dir, node_id, "tool_call", dict_content)

        log_path = tmp_path / "nodes" / node_id / "execution.log"
        assert log_path.exists()
        line = json.loads(log_path.read_text().strip())
        assert line["type"] == "tool_call"
        assert isinstance(line["content"], str)
        assert line["content"] == "list_colleagues({'department': 'eng'})"

    def test_string_content_writes_unchanged(self, tmp_path):
        """When content is a plain string, JSONL is unchanged (backward compat)."""
        from onemancompany.core.vessel import _append_node_execution_log

        project_dir = str(tmp_path)
        node_id = "node_002"

        _append_node_execution_log(project_dir, node_id, "llm_output", "Hello world")

        log_path = tmp_path / "nodes" / node_id / "execution.log"
        line = json.loads(log_path.read_text().strip())
        assert line["content"] == "Hello world"


class TestLogNodeDictContent:
    """_log_node should pass string to disk and dict to WS event."""

    def test_log_node_extracts_string_for_disk(self):
        """_log_node should pass string content to _append_node_execution_log."""
        from onemancompany.core.vessel import EmployeeManager, ScheduleEntry

        v = EmployeeManager.__new__(EmployeeManager)
        v._current_entries = {}
        v.executors = {}
        v._running_tasks = {}
        v._system_tasks = {}
        v._hooks = {}

        entry = ScheduleEntry(node_id="node_001", tree_path="/fake/tree.yaml")
        v._current_entries["emp_001"] = entry

        publish_calls = []
        v._publish_log_event = lambda emp_id, task_id, entry: publish_calls.append((emp_id, task_id, entry))

        with patch("onemancompany.core.task_tree.get_tree") as mock_get_tree, \
             patch("onemancompany.core.vessel._append_node_execution_log") as mock_append:
            mock_node = MagicMock()
            mock_node.project_dir = "/fake/project"
            mock_node.project_id = "proj_001"
            mock_tree = MagicMock()
            mock_tree.get_node.return_value = mock_node
            mock_get_tree.return_value = mock_tree

            v._log_node("emp_001", "node_001", "tool_call", {
                "tool_name": "dispatch_child",
                "tool_args": {"employee": "00006"},
                "content": "dispatch_child({'employee': '00006'})",
            })

            # Verify disk write got a string
            assert mock_append.called
            call_args = mock_append.call_args
            written_content = call_args[0][3]  # 4th positional arg
            assert isinstance(written_content, str)
            assert written_content == "dispatch_child({'employee': '00006'})"

        # Verify WS event got structured dict
        assert len(publish_calls) == 1
        _, _, pub_entry = publish_calls[0]
        assert pub_entry["type"] == "tool_call"
        assert isinstance(pub_entry["content"], dict)
        assert pub_entry["content"]["tool_name"] == "dispatch_child"
