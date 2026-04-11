"""Tests for node-level execution log (JSONL)."""

import json


def test_append_node_log(tmp_path):
    from onemancompany.core.vessel import _append_node_execution_log

    project_dir = str(tmp_path)
    _append_node_execution_log(
        project_dir,
        "node123",
        "tool_call",
        'dispatch_child({"target_employee_id": "00006", "description": "full content here"})',
    )
    log_path = tmp_path / "nodes" / "node123" / "execution.log"
    assert log_path.exists()
    line = json.loads(log_path.read_text().strip())
    assert line["type"] == "tool_call"
    assert "full content here" in line["content"]


def test_append_empty_project_dir():
    from onemancompany.core.vessel import _append_node_execution_log

    # Should not raise
    _append_node_execution_log("", "node123", "test", "content")


def test_multiple_entries(tmp_path):
    from onemancompany.core.vessel import _append_node_execution_log

    project_dir = str(tmp_path)
    _append_node_execution_log(project_dir, "node456", "start", "Task started")
    _append_node_execution_log(project_dir, "node456", "tool_call", 'read({"path": "/foo"})')
    _append_node_execution_log(project_dir, "node456", "result", "Done")
    log_path = tmp_path / "nodes" / "node456" / "execution.log"
    lines = [json.loads(l) for l in log_path.read_text().strip().split("\n")]
    assert len(lines) == 3
    assert lines[0]["type"] == "start"
    assert lines[2]["type"] == "result"
