"""Test tool call merging in CEO session history endpoint."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _write_execution_log(project_dir: Path, node_id: str, entries: list[dict]):
    """Write test execution log entries."""
    log_dir = project_dir / "nodes" / node_id
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "execution.log", "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def test_merge_tool_calls_into_history(tmp_path):
    """Tool calls from execution.log should be interleaved with conversation messages by timestamp."""
    from onemancompany.api.routes import _merge_tool_calls_into_history

    project_dir = tmp_path / "projects" / "proj_001"
    project_dir.mkdir(parents=True)

    # Conversation messages
    history = [
        {"role": "ceo", "text": "Build login page", "timestamp": "2026-04-07T10:00:00"},
        {"role": "system", "text": "Task dispatched", "source": "ea", "timestamp": "2026-04-07T10:00:05"},
    ]

    # Execution log with tool calls
    _write_execution_log(project_dir, "node_001", [
        {"ts": "2026-04-07T10:00:02", "type": "tool_call", "content": "dispatch_child({'employee': '00006'})"},
        {"ts": "2026-04-07T10:00:03", "type": "tool_result", "content": "dispatch_child \u2192 Task dispatched to \u5f20\u4e09"},
        {"ts": "2026-04-07T10:00:01", "type": "llm_input", "content": "[SystemMessage] You are EA..."},  # should be filtered
    ])

    # Mock tree to return node IDs
    mock_tree = MagicMock()
    mock_node = MagicMock()
    mock_node.id = "node_001"
    mock_node.project_dir = str(project_dir)
    mock_node.owner = "00004"
    mock_tree.all_nodes.return_value = [mock_node]

    merged = _merge_tool_calls_into_history(history, mock_tree, str(project_dir))

    # Should have 3 entries: ceo msg, tool_call (with result merged in), system msg
    # tool_result is paired into its tool_call, not a separate entry
    assert len(merged) == 3
    assert merged[0]["role"] == "ceo"
    assert merged[1]["type"] == "tool_call"
    assert merged[1]["tool_name"] == "dispatch_child"
    assert merged[1]["tool_result"] == "Task dispatched to 张三"  # result merged in
    assert merged[2]["role"] == "system"


def test_merge_empty_execution_log(tmp_path):
    """When no execution logs exist, history is returned unchanged."""
    from onemancompany.api.routes import _merge_tool_calls_into_history

    history = [
        {"role": "ceo", "text": "Hello", "timestamp": "2026-04-07T10:00:00"},
    ]

    mock_tree = MagicMock()
    mock_tree.all_nodes.return_value = []

    merged = _merge_tool_calls_into_history(history, mock_tree, str(tmp_path))
    assert len(merged) == 1
    assert merged[0]["role"] == "ceo"
