"""Unit tests for agents/common_tools.py — shared tools available to all employees."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.state import CompanyState, Employee, MeetingRoom, OfficeTool


def _make_cs() -> CompanyState:
    cs = CompanyState()
    cs._next_employee_number = 100
    return cs


def _make_emp(emp_id: str, **kwargs) -> Employee:
    defaults = dict(
        id=emp_id, name=f"Emp {emp_id}", role="Engineer",
        skills=["python"], employee_number=emp_id, nickname="测试",
    )
    defaults.update(kwargs)
    return Employee(**defaults)


# ---------------------------------------------------------------------------
# list_colleagues
# ---------------------------------------------------------------------------

class TestListColleagues:
    def test_returns_all_employees(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees = {
            "001": _make_emp("001", name="Alice", nickname="A", role="Engineer"),
            "002": _make_emp("002", name="Bob", nickname="B", role="Designer"),
        }
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = ct_mod.list_colleagues.invoke({})
        assert len(result) == 2
        names = {c["name"] for c in result}
        assert "Alice" in names
        assert "Bob" in names
        # Check all expected fields present
        for c in result:
            assert "id" in c
            assert "nickname" in c
            assert "role" in c
            assert "skills" in c

    def test_empty_company(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = ct_mod.list_colleagues.invoke({})
        assert result == []


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

class TestReadFile:
    def test_reads_existing_file(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("001", permissions=["company_file_access"])
        cs.employees["001"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        # Create a test file in company dir
        company_dir = tmp_path / "company"
        company_dir.mkdir()
        test_file = company_dir / "test.txt"
        test_file.write_text("hello world")

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: test_file if p == "test.txt" else None,
        )

        result = ct_mod.read_file.invoke({"file_path": "test.txt", "employee_id": "001"})
        assert result["status"] == "ok"
        assert result["content"] == "hello world"

    def test_access_denied(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: None,
        )

        result = ct_mod.read_file.invoke({"file_path": "secret.txt"})
        assert result["status"] == "error"
        assert "denied" in result["message"].lower() or "invalid" in result["message"].lower()

    def test_file_not_found(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        nonexistent = tmp_path / "nope.txt"
        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: nonexistent,
        )

        result = ct_mod.read_file.invoke({"file_path": "nope.txt"})
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------------

class TestListDirectory:
    def test_lists_directory_contents(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        # Create test directory
        test_dir = tmp_path / "testdir"
        test_dir.mkdir()
        (test_dir / "file1.txt").write_text("a")
        (test_dir / "subdir").mkdir()

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: test_dir,
        )

        result = ct_mod.list_directory.invoke({"dir_path": "testdir"})
        assert result["status"] == "ok"
        names = {e["name"] for e in result["entries"]}
        assert "file1.txt" in names
        assert "subdir" in names

    def test_skips_hidden_files(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        test_dir = tmp_path / "testdir"
        test_dir.mkdir()
        (test_dir / ".hidden").write_text("secret")
        (test_dir / "visible.txt").write_text("open")

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: test_dir,
        )

        result = ct_mod.list_directory.invoke({"dir_path": "testdir"})
        names = {e["name"] for e in result["entries"]}
        assert ".hidden" not in names
        assert "visible.txt" in names

    def test_access_denied(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: None,
        )

        result = ct_mod.list_directory.invoke({"dir_path": "secret/"})
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# save_to_project
# ---------------------------------------------------------------------------

class TestSaveToProject:
    def test_saves_file(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import config as config_mod

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj_dir = projects_dir / "myproj"
        proj_dir.mkdir()

        monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)

        result = ct_mod.save_to_project.invoke({
            "project_dir": str(proj_dir),
            "filename": "output.md",
            "content": "# Report\nDone.",
        })

        assert result["status"] == "ok"
        assert (proj_dir / "output.md").exists()
        assert (proj_dir / "output.md").read_text() == "# Report\nDone."

    def test_creates_subdirectories(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import config as config_mod

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj_dir = projects_dir / "myproj"
        proj_dir.mkdir()

        monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)

        result = ct_mod.save_to_project.invoke({
            "project_dir": str(proj_dir),
            "filename": "code/main.py",
            "content": "print('hi')",
        })

        assert result["status"] == "ok"
        assert (proj_dir / "code" / "main.py").exists()

    def test_rejects_path_outside_projects(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import config as config_mod

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)

        result = ct_mod.save_to_project.invoke({
            "project_dir": "/tmp/evil",
            "filename": "test.txt",
            "content": "bad",
        })
        assert result["status"] == "error"

    def test_rejects_path_traversal(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import config as config_mod

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj_dir = projects_dir / "myproj"
        proj_dir.mkdir()
        monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)

        result = ct_mod.save_to_project.invoke({
            "project_dir": str(proj_dir),
            "filename": "../../etc/passwd",
            "content": "bad",
        })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# list_project_workspace
# ---------------------------------------------------------------------------

class TestListProjectWorkspace:
    def test_lists_files(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import config as config_mod

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj_dir = projects_dir / "myproj"
        proj_dir.mkdir()
        (proj_dir / "readme.md").write_text("hello")
        (proj_dir / "code").mkdir()
        (proj_dir / "code" / "main.py").write_text("print()")

        monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)

        result = ct_mod.list_project_workspace.invoke({"project_dir": str(proj_dir)})
        assert result["status"] == "ok"
        assert "readme.md" in result["files"]
        # Should contain code/main.py as relative path
        assert any("main.py" in f for f in result["files"])

    def test_nonexistent_project_returns_empty(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import config as config_mod

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)

        result = ct_mod.list_project_workspace.invoke({
            "project_dir": str(projects_dir / "nonexistent"),
        })
        assert result["status"] == "ok"
        assert result["files"] == []

    def test_rejects_path_outside_projects(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import config as config_mod

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)

        result = ct_mod.list_project_workspace.invoke({"project_dir": "/tmp/evil"})
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# use_tool
# ---------------------------------------------------------------------------

class TestUseTool:
    def test_use_open_tool(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        tool = OfficeTool(
            id="t1", name="Open Tool", description="Available to all",
            added_by="COO", allowed_users=[], files=["readme.md"],
            folder_name="open_tool",
        )
        cs.tools["t1"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        tools_dir = tmp_path / "tools"
        tool_folder = tools_dir / "open_tool"
        tool_folder.mkdir(parents=True)
        (tool_folder / "readme.md").write_text("# Usage guide")
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)

        result = ct_mod.use_tool.invoke({
            "tool_name_or_id": "t1",
            "employee_id": "00010",
        })
        assert result["status"] == "ok"
        assert result["name"] == "Open Tool"
        assert "readme.md" in result["files"]
        assert result["files"]["readme.md"] == "# Usage guide"

    def test_denied_restricted_tool(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        tool = OfficeTool(
            id="t1", name="Secret Tool", description="Restricted",
            added_by="COO", allowed_users=["00099"], files=[], folder_name="",
        )
        cs.tools["t1"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = ct_mod.use_tool.invoke({
            "tool_name_or_id": "t1",
            "employee_id": "00010",
        })
        assert result["status"] == "denied"

    def test_lookup_by_name(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        tool = OfficeTool(
            id="t1", name="My Tool", description="Found by name",
            added_by="COO", allowed_users=[], files=[], folder_name="",
        )
        cs.tools["t1"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "TOOLS_DIR", Path("/nonexistent"))

        result = ct_mod.use_tool.invoke({
            "tool_name_or_id": "my tool",
            "employee_id": "00010",
        })
        assert result["status"] == "ok"
        assert result["name"] == "My Tool"

    def test_tool_not_found(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = ct_mod.use_tool.invoke({
            "tool_name_or_id": "nonexistent",
            "employee_id": "00010",
        })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# dispatch_task
# ---------------------------------------------------------------------------

class TestDispatchTask:
    def test_dispatches_to_registered_employee(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees["00010"] = _make_emp("00010", name="Alice")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        mock_loop = MagicMock()
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: mock_loop,
        )
        monkeypatch.setattr(ct_mod, "_current_loop", MagicMock(get=lambda: None))
        monkeypatch.setattr(ct_mod, "_current_task_id", MagicMock(get=lambda: None))

        result = ct_mod.dispatch_task.invoke({
            "employee_id": "00010",
            "task_description": "Write tests",
        })

        assert result["status"] == "dispatched"
        assert result["employee"] == "Alice"
        mock_loop.push_task.assert_called_once()

    def test_dispatch_employee_not_found(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: None,
        )

        result = ct_mod.dispatch_task.invoke({
            "employee_id": "99999",
            "task_description": "Do something",
        })
        assert result["status"] == "error"

    def test_dispatch_no_launcher(self, monkeypatch):
        """Employee exists but has no launcher and is not remote."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees["00010"] = _make_emp("00010", remote=False)
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: None,
        )

        result = ct_mod.dispatch_task.invoke({
            "employee_id": "00010",
            "task_description": "Do something",
        })
        assert result["status"] == "error"
        assert "No launcher" in result["message"]


# ---------------------------------------------------------------------------
# request_tool_access
# ---------------------------------------------------------------------------

class TestRequestToolAccess:
    def test_sends_request_to_coo(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", tool_permissions=[])
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        mock_coo_loop = MagicMock()
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: mock_coo_loop,
        )

        result = ct_mod.request_tool_access.invoke({
            "tool_name": "read_file",
            "reason": "Need to read project files",
            "employee_id": "00010",
        })

        assert result["status"] == "requested"
        mock_coo_loop.push_task.assert_called_once()

    def test_already_granted(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", tool_permissions=["read_file"])
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = ct_mod.request_tool_access.invoke({
            "tool_name": "read_file",
            "reason": "Need it",
            "employee_id": "00010",
        })
        assert result["status"] == "already_granted"

    def test_unknown_tool(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", tool_permissions=[])
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = ct_mod.request_tool_access.invoke({
            "tool_name": "nonexistent_tool",
            "reason": "Want it",
            "employee_id": "00010",
        })
        assert result["status"] == "error"

    def test_employee_not_found(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = ct_mod.request_tool_access.invoke({
            "tool_name": "read_file",
            "reason": "Need it",
            "employee_id": "99999",
        })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# manage_tool_access
# ---------------------------------------------------------------------------

class TestManageToolAccess:
    def test_grant_access(self, tmp_path, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core.config import COO_ID

        cs = _make_cs()
        emp = _make_emp("00010", tool_permissions=[])
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        monkeypatch.setattr(
            "onemancompany.core.config.update_tool_permissions",
            lambda eid, perms: None,
        )

        result = ct_mod.manage_tool_access.invoke({
            "employee_id": "00010",
            "tool_name": "read_file",
            "action": "grant",
            "manager_id": COO_ID,
        })

        assert result["status"] == "ok"
        assert "read_file" in emp.tool_permissions

    def test_revoke_access(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core.config import COO_ID

        cs = _make_cs()
        emp = _make_emp("00010", tool_permissions=["read_file", "use_tool"])
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        monkeypatch.setattr(
            "onemancompany.core.config.update_tool_permissions",
            lambda eid, perms: None,
        )

        result = ct_mod.manage_tool_access.invoke({
            "employee_id": "00010",
            "tool_name": "read_file",
            "action": "revoke",
            "manager_id": COO_ID,
        })

        assert result["status"] == "ok"
        assert "read_file" not in emp.tool_permissions
        assert "use_tool" in emp.tool_permissions

    def test_denied_non_coo(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = ct_mod.manage_tool_access.invoke({
            "employee_id": "00010",
            "tool_name": "read_file",
            "action": "grant",
            "manager_id": "00099",
        })
        assert result["status"] == "denied"

    def test_invalid_action(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core.config import COO_ID

        cs = _make_cs()
        emp = _make_emp("00010")
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = ct_mod.manage_tool_access.invoke({
            "employee_id": "00010",
            "tool_name": "read_file",
            "action": "delete",
            "manager_id": COO_ID,
        })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# create_subtask
# ---------------------------------------------------------------------------

class TestCreateSubtask:
    def test_queues_subtask(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_loop = MagicMock()
        mock_sub = MagicMock()
        mock_sub.id = "sub1"
        mock_loop.board.push.return_value = mock_sub

        monkeypatch.setattr(ct_mod, "_current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr(ct_mod, "_current_task_id", MagicMock(get=lambda: "parent1"))

        result = ct_mod.create_subtask.invoke({"description": "Do sub-work"})
        assert result["status"] == "queued"
        assert result["subtask_id"] == "sub1"

    def test_no_agent_loop_context(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        monkeypatch.setattr(ct_mod, "_current_loop", MagicMock(get=lambda: None))
        monkeypatch.setattr(ct_mod, "_current_task_id", MagicMock(get=lambda: None))

        result = ct_mod.create_subtask.invoke({"description": "Do work"})
        assert "error" in result


# ---------------------------------------------------------------------------
# Meeting helper functions
# ---------------------------------------------------------------------------

class TestMeetingHelpers:
    def test_build_employee_context(self):
        from onemancompany.agents.common_tools import _build_employee_context

        emp = _make_emp("001", name="Alice", nickname="A", department="Eng",
                        role="Engineer", level=2, work_principles="Be thorough")

        with patch("onemancompany.agents.common_tools.get_employee_skills_prompt", return_value=""):
            with patch("onemancompany.agents.common_tools.get_employee_tools_prompt", return_value=""):
                ctx = _build_employee_context(emp)

        assert "Alice" in ctx
        assert "Engineer" in ctx
        assert "Be thorough" in ctx

    def test_format_chat_history_empty(self):
        from onemancompany.agents.common_tools import _format_chat_history

        result = _format_chat_history([])
        assert "No discussion" in result

    def test_format_chat_history(self):
        from onemancompany.agents.common_tools import _format_chat_history

        history = [
            {"speaker": "Alice", "message": "Hello"},
            {"speaker": "Bob", "message": "Hi there"},
        ]
        result = _format_chat_history(history)
        assert "Alice: Hello" in result
        assert "Bob: Hi there" in result

    def test_build_evaluate_prompt(self):
        from onemancompany.agents.common_tools import _build_evaluate_prompt

        emp = _make_emp("001", name="Alice", nickname="A")

        with patch("onemancompany.agents.common_tools.get_employee_skills_prompt", return_value=""):
            with patch("onemancompany.agents.common_tools.get_employee_tools_prompt", return_value=""):
                prompt = _build_evaluate_prompt(emp, "Design review", "Review mockups", [])

        assert "Design review" in prompt
        assert "Review mockups" in prompt
        assert "YES" in prompt
        assert "NO" in prompt

    def test_build_speech_prompt(self):
        from onemancompany.agents.common_tools import _build_speech_prompt

        emp = _make_emp("001", name="Alice", nickname="A")

        with patch("onemancompany.agents.common_tools.get_employee_skills_prompt", return_value=""):
            with patch("onemancompany.agents.common_tools.get_employee_tools_prompt", return_value=""):
                prompt = _build_speech_prompt(emp, "Design review", "", [])

        assert "Design review" in prompt
        assert "perspective" in prompt


# ---------------------------------------------------------------------------
# pull_meeting
# ---------------------------------------------------------------------------

class TestPullMeeting:
    @pytest.mark.asyncio
    async def test_rejects_no_valid_participants(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Test",
            "participant_ids": ["99999"],
            "initiator_id": "",
        })
        assert result["status"] == "error"
        assert "No valid participants" in result["message"]

    @pytest.mark.asyncio
    async def test_rejects_solo_meeting(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees["001"] = _make_emp("001")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Test",
            "participant_ids": ["001"],
            "initiator_id": "001",
        })
        assert result["status"] == "error"
        assert "at least 2" in result["message"]

    @pytest.mark.asyncio
    async def test_denied_no_rooms_available(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees["001"] = _make_emp("001")
        cs.employees["002"] = _make_emp("002")
        cs.meeting_rooms = {}  # No rooms
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Test",
            "participant_ids": ["001", "002"],
        })
        assert result["status"] == "denied"


# ---------------------------------------------------------------------------
# Tool categorization
# ---------------------------------------------------------------------------

class TestToolCategorization:
    def test_base_tools_list(self):
        from onemancompany.agents.common_tools import BASE_TOOLS

        # Should have core tools
        names = {t.name for t in BASE_TOOLS}
        assert "list_colleagues" in names
        assert "save_to_project" in names
        assert "pull_meeting" in names
        assert "dispatch_task" in names

    def test_gated_tools_dict(self):
        from onemancompany.agents.common_tools import GATED_TOOLS

        assert "read_file" in GATED_TOOLS
        assert "list_directory" in GATED_TOOLS
        assert "use_tool" in GATED_TOOLS

    def test_common_tools_includes_all(self):
        from onemancompany.agents.common_tools import COMMON_TOOLS

        names = {t.name for t in COMMON_TOOLS}
        assert "list_colleagues" in names
        assert "read_file" in names
        assert "dispatch_task" in names
        assert "manage_tool_access" in names
