"""Unit tests for agents/tool_results.py — typed tool return values."""

from __future__ import annotations

from onemancompany.agents.tool_results import (
    DispatchTaskResult,
    ListDirectoryResult,
    MeetingResult,
    ReadFileResult,
    SaveFileResult,
    ToolError,
    ToolResult,
    ToolSuccess,
)


class TestToolSuccess:
    def test_default_status(self):
        s = ToolSuccess()
        assert s.status == "ok"
        assert s.message == ""

    def test_with_message(self):
        s = ToolSuccess(message="done")
        assert s.message == "done"

    def test_to_dict(self):
        s = ToolSuccess(message="done")
        d = s.model_dump()
        assert d == {"status": "ok", "message": "done"}


class TestToolError:
    def test_required_message(self):
        e = ToolError(message="something broke")
        assert e.status == "error"
        assert e.message == "something broke"
        assert e.code == "unknown"
        assert e.suggestion == ""

    def test_with_code_and_suggestion(self):
        e = ToolError(message="not found", code="404", suggestion="check path")
        assert e.code == "404"
        assert e.suggestion == "check path"

    def test_to_dict(self):
        e = ToolError(message="fail", code="err")
        d = e.model_dump()
        assert d["status"] == "error"
        assert d["message"] == "fail"
        assert d["code"] == "err"


class TestReadFileResult:
    def test_inherits_tool_success(self):
        r = ReadFileResult(path="/foo/bar.txt", content="hello", size=5)
        assert r.status == "ok"
        assert r.path == "/foo/bar.txt"
        assert r.content == "hello"
        assert r.size == 5

    def test_default_size(self):
        r = ReadFileResult(path="x", content="y")
        assert r.size == 0


class TestListDirectoryResult:
    def test_fields(self):
        r = ListDirectoryResult(path="/dir", entries=["a.py", "b.py"])
        assert r.status == "ok"
        assert r.path == "/dir"
        assert r.entries == ["a.py", "b.py"]

    def test_default_entries(self):
        r = ListDirectoryResult(path="/dir")
        assert r.entries == []


class TestSaveFileResult:
    def test_fields(self):
        r = SaveFileResult(path="/out.txt", bytes_written=42)
        assert r.status == "ok"
        assert r.bytes_written == 42

    def test_default_bytes(self):
        r = SaveFileResult(path="/out.txt")
        assert r.bytes_written == 0


class TestDispatchTaskResult:
    def test_fields(self):
        r = DispatchTaskResult(task_id="t1", assigned_to="00005", employee_name="Alice")
        assert r.status == "ok"
        assert r.task_id == "t1"
        assert r.assigned_to == "00005"
        assert r.employee_name == "Alice"

    def test_defaults(self):
        r = DispatchTaskResult()
        assert r.task_id == ""
        assert r.assigned_to == ""
        assert r.employee_name == ""


class TestMeetingResult:
    def test_fields(self):
        r = MeetingResult(meeting_id="m1", room_id="r1", participants=["Alice", "Bob"])
        assert r.status == "ok"
        assert r.meeting_id == "m1"
        assert r.room_id == "r1"
        assert r.participants == ["Alice", "Bob"]

    def test_defaults(self):
        r = MeetingResult()
        assert r.participants == []


class TestToolResultUnion:
    def test_success_is_tool_result(self):
        s = ToolSuccess()
        assert isinstance(s, ToolSuccess)

    def test_error_is_tool_result(self):
        e = ToolError(message="x")
        assert isinstance(e, ToolError)

    def test_specialized_is_tool_success(self):
        r = ReadFileResult(path="x", content="y")
        assert isinstance(r, ToolSuccess)
