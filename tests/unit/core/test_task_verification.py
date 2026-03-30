"""Tests for task verification — execution log evidence collection."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from onemancompany.core.task_verification import VerificationEvidence, collect_evidence


def _write_log(tmpdir: str, node_id: str, entries: list[dict]) -> None:
    log_dir = Path(tmpdir) / "nodes" / node_id
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "execution.log", "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


class TestCollectEvidence:
    def test_empty_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ev = collect_evidence(tmpdir, "node_abc")
            assert ev.tools_called == []
            assert ev.summary == "no tool activity"

    def test_tool_calls_tracked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_log(tmpdir, "node_abc", [
                {"type": "tool_call", "content": "web_search({'query': 'test'})"},
                {"type": "tool_result", "content": "web_search → {'status': 'ok', 'results': []}"},
                {"type": "tool_call", "content": "write({'file_path': '/tmp/out.md', 'content': 'hello'})"},
                {"type": "tool_result", "content": "write → {'status': 'ok'}"},
            ])
            ev = collect_evidence(tmpdir, "node_abc")
            assert "web_search" in ev.tools_called
            assert "write" in ev.tools_called
            assert "/tmp/out.md" in ev.files_written
            assert not ev.has_unresolved_errors

    def test_unresolved_error_tracked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_log(tmpdir, "node_abc", [
                {"type": "tool_call", "content": "bash({'command': 'python app.py'})"},
                {"type": "tool_result", "content": 'bash → {"status": "ok", "returncode": 1, "stdout": "", "stderr": "error"}'},
            ])
            ev = collect_evidence(tmpdir, "node_abc")
            assert ev.has_unresolved_errors
            assert ev.unresolved_errors[0]["tool"] == "bash"
            assert ev.commands_run[0]["exit_code"] == 1

    def test_error_resolved_by_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_log(tmpdir, "node_abc", [
                {"type": "tool_call", "content": "bash({'command': 'python app.py'})"},
                {"type": "tool_result", "content": 'bash → {"returncode": 1, "stdout": ""}'},
                {"type": "tool_call", "content": "bash({'command': 'python app.py'})"},
                {"type": "tool_result", "content": 'bash → {"returncode": 0, "stdout": "ok"}'},
            ])
            ev = collect_evidence(tmpdir, "node_abc")
            assert not ev.has_unresolved_errors  # second bash succeeded → error cleared

    def test_no_log_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ev = collect_evidence(tmpdir, "nonexistent_node")
            assert ev.tools_called == []


class TestVerificationEvidence:
    def test_summary(self):
        ev = VerificationEvidence(
            tools_called=["write", "bash"],
            tools_succeeded=["write"],
            files_written=["/tmp/out.md"],
            unresolved_errors=[{"tool": "bash", "error": "exit code 1"}],
        )
        assert "2 tool calls" in ev.summary
        assert "1 unresolved error" in ev.summary

    def test_to_review_block(self):
        ev = VerificationEvidence(
            tools_called=["write", "bash"],
            tools_succeeded=["write"],
            files_written=["/tmp/out.md"],
            commands_run=[{"cmd": "python app.py", "exit_code": 1}],
            unresolved_errors=[{"tool": "bash", "error": "exit code 1"}],
        )
        block = ev.to_review_block()
        assert "[Verification Evidence]" in block
        assert "UNRESOLVED ERRORS" in block
        assert "bash" in block
        assert "File written: /tmp/out.md" in block

    def test_to_review_block_no_tools(self):
        ev = VerificationEvidence()
        block = ev.to_review_block()
        assert "No tools were called" in block

    def test_to_dict_roundtrip(self):
        ev = VerificationEvidence(
            tools_called=["write"],
            files_written=["/tmp/f.txt"],
        )
        d = ev.to_dict()
        ev2 = VerificationEvidence(**d)
        assert ev2.tools_called == ["write"]
        assert ev2.files_written == ["/tmp/f.txt"]
