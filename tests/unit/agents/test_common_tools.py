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


# ---------------------------------------------------------------------------
# Additional coverage: _publish and _chat helpers
# ---------------------------------------------------------------------------

class TestPublishAndChat:
    @pytest.mark.asyncio
    async def test_publish_fires_event(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_bus = MagicMock(publish=AsyncMock())
        monkeypatch.setattr(ct_mod, "event_bus", mock_bus)

        await ct_mod._publish("test_event", {"key": "val"}, agent="TEST")
        mock_bus.publish.assert_awaited_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.type == "test_event"
        assert event.payload == {"key": "val"}
        assert event.agent == "TEST"

    @pytest.mark.asyncio
    async def test_chat_fires_meeting_chat_event(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_bus = MagicMock(publish=AsyncMock())
        monkeypatch.setattr(ct_mod, "event_bus", mock_bus)

        await ct_mod._chat("room-1", "Alice", "Engineer", "Hello")
        mock_bus.publish.assert_awaited_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.type == "meeting_chat"
        assert event.payload["room_id"] == "room-1"
        assert event.payload["speaker"] == "Alice"


# ---------------------------------------------------------------------------
# Additional coverage: read_file edge cases
# ---------------------------------------------------------------------------

class TestReadFileAdditional:
    def test_read_not_a_file(self, tmp_path, monkeypatch):
        """Resolves to a directory, not a file."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        test_dir = tmp_path / "adir"
        test_dir.mkdir()

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: test_dir,
        )

        result = ct_mod.read_file.invoke({"file_path": "adir"})
        assert result["status"] == "error"
        assert "Not a file" in result["message"]

    def test_read_exception(self, tmp_path, monkeypatch):
        """Test read failure (e.g. encoding issue)."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        test_file = tmp_path / "bad.bin"
        test_file.write_bytes(b"\x80\x81\x82")

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.is_file.return_value = True
        mock_path.read_text.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "bad")

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: mock_path,
        )

        result = ct_mod.read_file.invoke({"file_path": "bad.bin"})
        assert result["status"] == "error"
        assert "Read failed" in result["message"]

    def test_read_no_employee_id(self, tmp_path, monkeypatch):
        """No employee_id means empty permissions."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        test_file = tmp_path / "open.txt"
        test_file.write_text("content")

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: test_file,
        )

        result = ct_mod.read_file.invoke({"file_path": "open.txt"})
        assert result["status"] == "ok"
        assert result["content"] == "content"


# ---------------------------------------------------------------------------
# Additional coverage: list_directory edge cases
# ---------------------------------------------------------------------------

class TestListDirectoryAdditional:
    def test_directory_not_found(self, tmp_path, monkeypatch):
        """Resolved path exists but is not a directory."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        test_file = tmp_path / "afile.txt"
        test_file.write_text("data")

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: test_file,
        )

        result = ct_mod.list_directory.invoke({"dir_path": "afile.txt"})
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_directory_exception(self, monkeypatch):
        """iterdir raises an exception."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        mock_dir = MagicMock()
        mock_dir.exists.return_value = True
        mock_dir.is_dir.return_value = True
        mock_dir.iterdir.side_effect = PermissionError("no access")

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: mock_dir,
        )

        result = ct_mod.list_directory.invoke({"dir_path": "locked"})
        assert result["status"] == "error"
        assert "Failed to read" in result["message"]

    def test_with_employee_permissions(self, tmp_path, monkeypatch):
        """Employee with permissions gets their permissions passed through."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("001", permissions=["backend_code_maintenance"])
        cs.employees["001"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        test_dir = tmp_path / "src"
        test_dir.mkdir()
        (test_dir / "main.py").write_text("code")

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: test_dir,
        )

        result = ct_mod.list_directory.invoke({"dir_path": "src", "employee_id": "001"})
        assert result["status"] == "ok"

    def test_empty_dir_path_defaults(self, tmp_path, monkeypatch):
        """Empty dir_path defaults to '.'."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: tmp_path,
        )

        result = ct_mod.list_directory.invoke({})
        assert result["status"] == "ok"
        assert result["path"] == "."

    def test_directory_entries_type_classification(self, tmp_path, monkeypatch):
        """Verify files and dirs are classified correctly."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        test_dir = tmp_path / "mixed"
        test_dir.mkdir()
        (test_dir / "file.txt").write_text("data")
        (test_dir / "subdir").mkdir()

        monkeypatch.setattr(
            "onemancompany.core.file_editor._resolve_path",
            lambda p, permissions=None: test_dir,
        )

        result = ct_mod.list_directory.invoke({"dir_path": "mixed"})
        entries_by_name = {e["name"]: e for e in result["entries"]}
        assert entries_by_name["file.txt"]["type"] == "file"
        assert entries_by_name["subdir"]["type"] == "dir"


# ---------------------------------------------------------------------------
# Additional coverage: propose_file_edit
# ---------------------------------------------------------------------------

class TestProposeFileEdit:
    @pytest.mark.asyncio
    async def test_propose_edit_success_with_project_context(self, monkeypatch):
        """propose_file_edit succeeds and edit is collected for batch resolution."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("001", permissions=["company_file_access"])
        cs.employees["001"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        edit_result = {"status": "pending_approval", "edit_id": "e123"}
        monkeypatch.setattr(
            "onemancompany.core.file_editor.propose_edit",
            lambda *a, **kw: edit_result,
        )

        fake_edit = {"edit_id": "e123", "rel_path": "test.txt", "reason": "fix", "proposed_by": "agent", "old_content": "a", "new_content": "b"}
        monkeypatch.setattr(
            "onemancompany.core.file_editor.pending_file_edits",
            {"e123": fake_edit},
        )

        # Simulate project context
        mock_pid = MagicMock()
        mock_pid.get.return_value = "proj-1"
        monkeypatch.setattr("onemancompany.core.resolutions.current_project_id", mock_pid)
        mock_collect = MagicMock()
        monkeypatch.setattr("onemancompany.core.resolutions.collect_edit", mock_collect)

        result = await ct_mod.propose_file_edit.ainvoke({
            "file_path": "test.txt",
            "new_content": "new stuff",
            "reason": "fix bug",
            "employee_id": "001",
        })
        assert result["status"] == "pending_approval"
        mock_collect.assert_called_once_with("proj-1", fake_edit)

    @pytest.mark.asyncio
    async def test_propose_edit_no_project_context_publishes_event(self, monkeypatch):
        """Without project context, publishes event directly."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        edit_result = {"status": "pending_approval", "edit_id": "e456"}
        monkeypatch.setattr(
            "onemancompany.core.file_editor.propose_edit",
            lambda *a, **kw: edit_result,
        )

        fake_edit = {"edit_id": "e456", "rel_path": "f.txt", "reason": "r", "proposed_by": "agent", "old_content": "", "new_content": "x"}
        monkeypatch.setattr(
            "onemancompany.core.file_editor.pending_file_edits",
            {"e456": fake_edit},
        )

        mock_pid = MagicMock()
        mock_pid.get.return_value = ""
        monkeypatch.setattr("onemancompany.core.resolutions.current_project_id", mock_pid)

        mock_publish = AsyncMock()
        monkeypatch.setattr(ct_mod, "_publish", mock_publish)

        result = await ct_mod.propose_file_edit.ainvoke({
            "file_path": "f.txt",
            "new_content": "x",
            "reason": "r",
        })
        assert result["status"] == "pending_approval"
        mock_publish.assert_awaited_once()
        assert mock_publish.call_args[0][0] == "file_edit_proposed"

    @pytest.mark.asyncio
    async def test_propose_edit_error(self, monkeypatch):
        """propose_edit returns error status."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        monkeypatch.setattr(
            "onemancompany.core.file_editor.propose_edit",
            lambda *a, **kw: {"status": "error", "message": "Access denied"},
        )

        result = await ct_mod.propose_file_edit.ainvoke({
            "file_path": "secret.txt",
            "new_content": "x",
            "reason": "r",
        })
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_propose_edit_pending_but_edit_not_found(self, monkeypatch):
        """Edit pending_approval but edit_id not in pending_file_edits returns result as-is."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        edit_result = {"status": "pending_approval", "edit_id": "missing"}
        monkeypatch.setattr(
            "onemancompany.core.file_editor.propose_edit",
            lambda *a, **kw: edit_result,
        )
        monkeypatch.setattr(
            "onemancompany.core.file_editor.pending_file_edits",
            {},
        )

        result = await ct_mod.propose_file_edit.ainvoke({
            "file_path": "f.txt",
            "new_content": "x",
            "reason": "r",
        })
        assert result["status"] == "pending_approval"


# ---------------------------------------------------------------------------
# Additional coverage: use_tool edge cases
# ---------------------------------------------------------------------------

class TestUseToolAdditional:
    def test_lookup_by_folder_name(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        tool = OfficeTool(
            id="t1", name="Special Tool", description="Found by folder",
            added_by="COO", allowed_users=[], files=[], folder_name="special_folder",
        )
        cs.tools["t1"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "TOOLS_DIR", Path("/nonexistent"))

        result = ct_mod.use_tool.invoke({
            "tool_name_or_id": "special_folder",
            "employee_id": "00010",
        })
        assert result["status"] == "ok"
        assert result["name"] == "Special Tool"

    def test_binary_file_in_tool(self, tmp_path, monkeypatch):
        """Binary files should report size instead of content."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        tool = OfficeTool(
            id="t2", name="BinTool", description="Has binary",
            added_by="COO", allowed_users=[], files=["image.png"],
            folder_name="bintool",
        )
        cs.tools["t2"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        tools_dir = tmp_path / "tools"
        tool_folder = tools_dir / "bintool"
        tool_folder.mkdir(parents=True)
        bin_file = tool_folder / "image.png"
        bin_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)

        # Make read_text raise UnicodeDecodeError
        original_read = Path.read_text

        def mock_read_text(self_path, encoding="utf-8"):
            if self_path.name == "image.png":
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "invalid")
            return original_read(self_path, encoding=encoding)

        monkeypatch.setattr(Path, "read_text", mock_read_text)

        result = ct_mod.use_tool.invoke({
            "tool_name_or_id": "t2",
            "employee_id": "00010",
        })
        assert result["status"] == "ok"
        assert "binary file" in result["files"]["image.png"]

    def test_file_not_in_folder(self, tmp_path, monkeypatch):
        """Listed file doesn't exist on disk — skipped."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        tool = OfficeTool(
            id="t3", name="MissingFiles", description="Files missing",
            added_by="COO", allowed_users=[], files=["ghost.txt"],
            folder_name="missing_files",
        )
        cs.tools["t3"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        tools_dir = tmp_path / "tools"
        tool_folder = tools_dir / "missing_files"
        tool_folder.mkdir(parents=True)
        # Don't create ghost.txt
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)

        result = ct_mod.use_tool.invoke({
            "tool_name_or_id": "t3",
            "employee_id": "00010",
        })
        assert result["status"] == "ok"
        assert "ghost.txt" not in result["files"]


# ---------------------------------------------------------------------------
# Additional coverage: manage_tool_access edge cases
# ---------------------------------------------------------------------------

class TestManageToolAccessAdditional:
    def test_grant_when_tool_permissions_is_none(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core.config import COO_ID

        cs = _make_cs()
        emp = _make_emp("00010", tool_permissions=None)
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
        assert emp.tool_permissions == ["read_file"]

    def test_employee_not_found(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core.config import COO_ID

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = ct_mod.manage_tool_access.invoke({
            "employee_id": "99999",
            "tool_name": "read_file",
            "action": "grant",
            "manager_id": COO_ID,
        })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Additional coverage: request_tool_access edge cases
# ---------------------------------------------------------------------------

class TestRequestToolAccessAdditional:
    def test_coo_not_available(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", tool_permissions=[])
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: None,
        )

        result = ct_mod.request_tool_access.invoke({
            "tool_name": "read_file",
            "reason": "Need it",
            "employee_id": "00010",
        })
        assert result["status"] == "error"
        assert "not available" in result["message"]


# ---------------------------------------------------------------------------
# Additional coverage: set_acceptance_criteria
# ---------------------------------------------------------------------------

class TestSetAcceptanceCriteria:
    def test_no_context(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: None))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: None))

        result = ct_mod.set_acceptance_criteria.invoke({
            "criteria": ["test"],
            "responsible_officer_id": "00003",
        })
        assert result["status"] == "error"

    def test_no_task(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = None
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        result = ct_mod.set_acceptance_criteria.invoke({
            "criteria": ["test"],
            "responsible_officer_id": "00003",
        })
        assert result["status"] == "error"

    def test_no_project(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_task = MagicMock()
        mock_task.project_id = ""
        mock_task.original_project_id = ""

        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = mock_task
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        result = ct_mod.set_acceptance_criteria.invoke({
            "criteria": ["test"],
            "responsible_officer_id": "00003",
        })
        assert result["status"] == "error"
        assert "project" in result["message"].lower()

    def test_success(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_task = MagicMock()
        mock_task.project_id = "proj-1"
        mock_task.original_project_id = ""

        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = mock_task
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        mock_set = MagicMock()
        monkeypatch.setattr("onemancompany.core.project_archive.set_acceptance_criteria", mock_set)

        result = ct_mod.set_acceptance_criteria.invoke({
            "criteria": ["criterion 1", "criterion 2"],
            "responsible_officer_id": "00003",
        })
        assert result["status"] == "ok"
        assert result["criteria_count"] == 2
        mock_set.assert_called_once()


# ---------------------------------------------------------------------------
# Additional coverage: accept_project
# ---------------------------------------------------------------------------

class TestAcceptProject:
    def test_no_context(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: None))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: None))

        result = ct_mod.accept_project.invoke({"accepted": True})
        assert result["status"] == "error"

    def test_no_task(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = None
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        result = ct_mod.accept_project.invoke({"accepted": True})
        assert result["status"] == "error"

    def test_no_project(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_task = MagicMock()
        mock_task.project_id = ""
        mock_task.original_project_id = ""
        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = mock_task
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        result = ct_mod.accept_project.invoke({"accepted": True})
        assert result["status"] == "error"

    def test_accepted(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_task = MagicMock()
        mock_task.project_id = "proj-1"
        mock_task.original_project_id = ""
        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = mock_task
        mock_loop.agent.employee_id = "00003"
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        mock_set = MagicMock()
        monkeypatch.setattr("onemancompany.core.project_archive.set_acceptance_result", mock_set)

        result = ct_mod.accept_project.invoke({"accepted": True, "notes": "looks good"})
        assert result["status"] == "accepted"
        assert result["project_id"] == "proj-1"

    def test_rejected(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_task = MagicMock()
        mock_task.project_id = ""
        mock_task.original_project_id = "proj-2"
        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = mock_task
        mock_loop.agent.employee_id = "00003"
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        mock_set = MagicMock()
        monkeypatch.setattr("onemancompany.core.project_archive.set_acceptance_result", mock_set)

        result = ct_mod.accept_project.invoke({"accepted": False, "notes": "needs work"})
        assert result["status"] == "rejected"


# ---------------------------------------------------------------------------
# Additional coverage: ea_review_project
# ---------------------------------------------------------------------------

class TestEaReviewProject:
    def test_no_context(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: None))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: None))

        result = ct_mod.ea_review_project.invoke({"approved": True, "review_notes": "ok"})
        assert result["status"] == "error"

    def test_no_task(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = None
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        result = ct_mod.ea_review_project.invoke({"approved": True, "review_notes": "ok"})
        assert result["status"] == "error"

    def test_no_project(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_task = MagicMock()
        mock_task.project_id = ""
        mock_task.original_project_id = ""
        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = mock_task
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        result = ct_mod.ea_review_project.invoke({"approved": True, "review_notes": "ok"})
        assert result["status"] == "error"

    def test_approved(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_task = MagicMock()
        mock_task.project_id = "proj-1"
        mock_task.original_project_id = ""
        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = mock_task
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        mock_set = MagicMock()
        monkeypatch.setattr("onemancompany.core.project_archive.set_ea_review_result", mock_set)

        result = ct_mod.ea_review_project.invoke({"approved": True, "review_notes": "all good"})
        assert result["status"] == "approved"

    def test_rejected(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_task = MagicMock()
        mock_task.project_id = ""
        mock_task.original_project_id = "proj-2"
        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = mock_task
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        mock_set = MagicMock()
        monkeypatch.setattr("onemancompany.core.project_archive.set_ea_review_result", mock_set)

        result = ct_mod.ea_review_project.invoke({"approved": False, "review_notes": "needs fixes"})
        assert result["status"] == "rejected"


# ---------------------------------------------------------------------------
# Additional coverage: set_project_budget
# ---------------------------------------------------------------------------

class TestSetProjectBudget:
    def test_no_context(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: None))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: None))

        result = ct_mod.set_project_budget.invoke({"budget_usd": 10.0})
        assert result["status"] == "error"

    def test_no_task(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = None
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        result = ct_mod.set_project_budget.invoke({"budget_usd": 10.0})
        assert result["status"] == "error"

    def test_no_project(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_task = MagicMock()
        mock_task.project_id = ""
        mock_task.original_project_id = ""
        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = mock_task
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        result = ct_mod.set_project_budget.invoke({"budget_usd": 10.0})
        assert result["status"] == "error"

    def test_success(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod

        mock_task = MagicMock()
        mock_task.project_id = "proj-1"
        mock_task.original_project_id = ""
        mock_loop = MagicMock()
        mock_loop.board.get_task.return_value = mock_task
        monkeypatch.setattr("onemancompany.core.agent_loop._current_loop", MagicMock(get=lambda: mock_loop))
        monkeypatch.setattr("onemancompany.core.agent_loop._current_task_id", MagicMock(get=lambda: "task-1"))

        mock_set = MagicMock()
        monkeypatch.setattr("onemancompany.core.project_archive.set_project_budget", mock_set)

        result = ct_mod.set_project_budget.invoke({"budget_usd": 25.5})
        assert result["status"] == "ok"
        assert result["budget_usd"] == 25.5


# ---------------------------------------------------------------------------
# Additional coverage: dispatch_task (remote employee)
# ---------------------------------------------------------------------------

class TestDispatchTaskAdditional:
    def test_dispatch_to_remote_employee(self, monkeypatch):
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees["00010"] = _make_emp("00010", name="RemoteAlice", remote=True)
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: None,
        )
        monkeypatch.setattr(ct_mod, "_current_loop", MagicMock(get=lambda: None))
        monkeypatch.setattr(ct_mod, "_current_task_id", MagicMock(get=lambda: None))

        # Mock _remote_task_queues
        remote_queues = {}
        monkeypatch.setattr("onemancompany.api.routes._remote_task_queues", remote_queues)

        result = ct_mod.dispatch_task.invoke({
            "employee_id": "00010",
            "task_description": "Remote work",
        })
        assert result["status"] == "dispatched_remote"
        assert "00010" in remote_queues
        assert len(remote_queues["00010"]) == 1

    def test_dispatch_with_project_context(self, monkeypatch):
        """Dispatch inherits project context from caller task."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import agent_loop as al_mod

        cs = _make_cs()
        cs.employees["00010"] = _make_emp("00010", name="Alice")
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        mock_target_loop = MagicMock()
        monkeypatch.setattr(al_mod, "get_agent_loop", lambda eid: mock_target_loop)

        # Set up caller context
        mock_caller_task = MagicMock()
        mock_caller_task.project_id = "proj-1"
        mock_caller_task.original_project_id = ""
        mock_caller_task.project_dir = "/some/dir"
        mock_caller_task.original_project_dir = ""

        mock_caller_loop = MagicMock()
        mock_caller_loop.board.get_task.return_value = mock_caller_task

        # dispatch_task re-imports from agent_loop, so patch at that level
        monkeypatch.setattr(al_mod, "_current_loop", MagicMock(get=lambda: mock_caller_loop))
        monkeypatch.setattr(al_mod, "_current_task_id", MagicMock(get=lambda: "caller-task"))

        mock_record = MagicMock()
        monkeypatch.setattr("onemancompany.core.project_archive.record_dispatch", mock_record)

        result = ct_mod.dispatch_task.invoke({
            "employee_id": "00010",
            "task_description": "Build feature",
        })
        assert result["status"] == "dispatched"
        mock_record.assert_called_once()
        # project_id should be moved to original
        assert mock_caller_task.original_project_id == "proj-1"
        assert mock_caller_task.project_id == ""

    def test_dispatch_remote_with_project_context(self, monkeypatch):
        """Remote dispatch also inherits project context."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import agent_loop as al_mod

        cs = _make_cs()
        cs.employees["00010"] = _make_emp("00010", name="RemoteBob", remote=True)
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)
        monkeypatch.setattr(al_mod, "get_agent_loop", lambda eid: None)

        mock_caller_task = MagicMock()
        mock_caller_task.project_id = "proj-2"
        mock_caller_task.original_project_id = ""
        mock_caller_task.project_dir = "/proj/dir"
        mock_caller_task.original_project_dir = ""

        mock_caller_loop = MagicMock()
        mock_caller_loop.board.get_task.return_value = mock_caller_task

        monkeypatch.setattr(al_mod, "_current_loop", MagicMock(get=lambda: mock_caller_loop))
        monkeypatch.setattr(al_mod, "_current_task_id", MagicMock(get=lambda: "caller-task"))

        remote_queues = {}
        monkeypatch.setattr("onemancompany.api.routes._remote_task_queues", remote_queues)
        mock_record = MagicMock()
        monkeypatch.setattr("onemancompany.core.project_archive.record_dispatch", mock_record)

        result = ct_mod.dispatch_task.invoke({
            "employee_id": "00010",
            "task_description": "Remote task",
        })
        assert result["status"] == "dispatched_remote"
        mock_record.assert_called_once()
        assert remote_queues["00010"][0]["project_id"] == "proj-2"


# ---------------------------------------------------------------------------
# Additional coverage: pull_meeting (full meeting flow)
# ---------------------------------------------------------------------------

class TestPullMeetingFull:
    @pytest.mark.asyncio
    async def test_successful_meeting_flow(self, monkeypatch):
        """Full meeting: booking, discussion rounds, summary, room release."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp1 = _make_emp("001", name="Alice", nickname="小A", role="Engineer", level=2)
        emp2 = _make_emp("002", name="Bob", nickname="小B", role="Designer", level=1)
        cs.employees["001"] = emp1
        cs.employees["002"] = emp2
        room = MeetingRoom(
            id="r1", name="Room A", description="Small room",
            capacity=6, is_booked=False,
        )
        cs.meeting_rooms["r1"] = room
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        # Track evaluate calls
        eval_count = 0

        async def mock_tracked_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal eval_count
            resp = MagicMock()
            if "YES or NO" in prompt or "YES" in prompt and "NO" in prompt:
                # Evaluate prompt — say YES once, then NO
                eval_count += 1
                if eval_count <= 2:
                    resp.content = "YES\nI have something to say"
                else:
                    resp.content = "NO\nNothing more"
            elif "perspective" in prompt or "share your" in prompt.lower():
                resp.content = "I think we should prioritize testing."
            elif "Summarize" in prompt or "note-taker" in prompt:
                resp.content = 'Meeting went well. [{"assignee": "Alice", "action": "write tests"}]'
            else:
                resp.content = "Generic response"
            return resp

        monkeypatch.setattr(ct_mod, "tracked_ainvoke", mock_tracked_ainvoke)
        monkeypatch.setattr(ct_mod, "make_llm", lambda emp_id: MagicMock())
        monkeypatch.setattr(ct_mod, "get_employee_skills_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "get_employee_tools_prompt", lambda eid: "")

        mock_publish = AsyncMock()
        monkeypatch.setattr(ct_mod, "_publish", mock_publish)
        mock_chat = AsyncMock()
        monkeypatch.setattr(ct_mod, "_chat", mock_chat)

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Sprint planning",
            "participant_ids": ["001", "002"],
            "agenda": "Plan next sprint",
            "initiator_id": "001",
        })

        assert result["status"] == "completed"
        assert result["topic"] == "Sprint planning"
        assert result["room"] == "Room A"
        assert len(result["participants"]) == 2
        assert len(result["action_items"]) >= 1
        # Room should be released
        assert room.is_booked is False
        assert room.booked_by == ""

    @pytest.mark.asyncio
    async def test_meeting_max_rounds(self, monkeypatch):
        """Meeting reaches max rounds and ends."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp1 = _make_emp("001", name="Alice", nickname="小A")
        emp2 = _make_emp("002", name="Bob", nickname="小B")
        cs.employees["001"] = emp1
        cs.employees["002"] = emp2
        room = MeetingRoom(
            id="r1", name="Room A", description="Small",
            capacity=6, is_booked=False,
        )
        cs.meeting_rooms["r1"] = room
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        async def always_yes_ainvoke(llm, prompt, category="", employee_id=""):
            resp = MagicMock()
            if "YES or NO" in prompt or ("YES" in prompt and "NO" in prompt):
                resp.content = "YES\nMore to say"
            elif "note-taker" in prompt or "Summarize" in prompt:
                resp.content = "Summary. []"
            else:
                resp.content = "My input"
            return resp

        monkeypatch.setattr(ct_mod, "tracked_ainvoke", always_yes_ainvoke)
        monkeypatch.setattr(ct_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(ct_mod, "get_employee_skills_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "get_employee_tools_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "_publish", AsyncMock())
        monkeypatch.setattr(ct_mod, "_chat", AsyncMock())

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Infinite discussion",
            "participant_ids": ["001", "002"],
            "initiator_id": "001",
        })

        assert result["status"] == "completed"
        assert result["rounds"] == 15

    @pytest.mark.asyncio
    async def test_meeting_no_one_wants_to_speak(self, monkeypatch):
        """All participants say NO immediately."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp1 = _make_emp("001", name="Alice", nickname="小A")
        emp2 = _make_emp("002", name="Bob", nickname="小B")
        cs.employees["001"] = emp1
        cs.employees["002"] = emp2
        room = MeetingRoom(
            id="r1", name="Room A", description="Small",
            capacity=6, is_booked=False,
        )
        cs.meeting_rooms["r1"] = room
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        async def always_no(llm, prompt, category="", employee_id=""):
            resp = MagicMock()
            if "note-taker" in prompt or "Summarize" in prompt:
                resp.content = "Nothing discussed. []"
            else:
                resp.content = "NO\nNothing to say"
            return resp

        monkeypatch.setattr(ct_mod, "tracked_ainvoke", always_no)
        monkeypatch.setattr(ct_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(ct_mod, "get_employee_skills_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "get_employee_tools_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "_publish", AsyncMock())
        monkeypatch.setattr(ct_mod, "_chat", AsyncMock())

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Quick sync",
            "participant_ids": ["001", "002"],
            "initiator_id": "001",
        })

        assert result["status"] == "completed"
        assert result["rounds"] == 1
        assert len(result["discussion"]) == 0

    @pytest.mark.asyncio
    async def test_meeting_room_released_on_error(self, monkeypatch):
        """Room is released even if meeting processing fails."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp1 = _make_emp("001", name="Alice", nickname="小A")
        emp2 = _make_emp("002", name="Bob", nickname="小B")
        cs.employees["001"] = emp1
        cs.employees["002"] = emp2
        room = MeetingRoom(
            id="r1", name="Room A", description="Small",
            capacity=6, is_booked=False,
        )
        cs.meeting_rooms["r1"] = room
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        async def failing_ainvoke(llm, prompt, category="", employee_id=""):
            raise RuntimeError("LLM crashed")

        monkeypatch.setattr(ct_mod, "tracked_ainvoke", failing_ainvoke)
        monkeypatch.setattr(ct_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(ct_mod, "get_employee_skills_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "get_employee_tools_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "_publish", AsyncMock())
        monkeypatch.setattr(ct_mod, "_chat", AsyncMock())

        with pytest.raises(RuntimeError):
            await ct_mod.pull_meeting.ainvoke({
                "topic": "Broken meeting",
                "participant_ids": ["001", "002"],
                "initiator_id": "001",
            })

        # Room should still be released
        assert room.is_booked is False

    @pytest.mark.asyncio
    async def test_meeting_with_capacity_too_small(self, monkeypatch):
        """All rooms are too small for participants."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp1 = _make_emp("001", name="Alice", nickname="小A")
        emp2 = _make_emp("002", name="Bob", nickname="小B")
        cs.employees["001"] = emp1
        cs.employees["002"] = emp2
        room = MeetingRoom(
            id="r1", name="Tiny Room", description="Very small",
            capacity=1, is_booked=False,  # Only fits 1 person
        )
        cs.meeting_rooms["r1"] = room
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Crowded meeting",
            "participant_ids": ["001", "002"],
            "initiator_id": "001",
        })

        assert result["status"] == "denied"

    @pytest.mark.asyncio
    async def test_meeting_bad_json_in_summary(self, monkeypatch):
        """Summary with invalid JSON still completes gracefully."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp1 = _make_emp("001", name="Alice", nickname="小A")
        emp2 = _make_emp("002", name="Bob", nickname="小B")
        cs.employees["001"] = emp1
        cs.employees["002"] = emp2
        room = MeetingRoom(
            id="r1", name="Room A", description="Small",
            capacity=6, is_booked=False,
        )
        cs.meeting_rooms["r1"] = room
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        call_count = 0

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if "YES or NO" in prompt or ("YES" in prompt and "NO" in prompt):
                resp.content = "YES" if call_count <= 2 else "NO"
            elif "note-taker" in prompt or "Summarize" in prompt:
                resp.content = "Summary done. [invalid json content]"
            else:
                resp.content = "My contribution"
            return resp

        monkeypatch.setattr(ct_mod, "tracked_ainvoke", mock_ainvoke)
        monkeypatch.setattr(ct_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(ct_mod, "get_employee_skills_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "get_employee_tools_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "_publish", AsyncMock())
        monkeypatch.setattr(ct_mod, "_chat", AsyncMock())

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Test",
            "participant_ids": ["001", "002"],
            "initiator_id": "001",
        })

        assert result["status"] == "completed"
        assert result["action_items"] == []

    @pytest.mark.asyncio
    async def test_meeting_no_initiator(self, monkeypatch):
        """Meeting without initiator_id — first participant is used."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp1 = _make_emp("001", name="Alice", nickname="小A")
        emp2 = _make_emp("002", name="Bob", nickname="小B")
        cs.employees["001"] = emp1
        cs.employees["002"] = emp2
        room = MeetingRoom(
            id="r1", name="Room A", description="Small",
            capacity=6, is_booked=False,
        )
        cs.meeting_rooms["r1"] = room
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        async def quick_no(llm, prompt, category="", employee_id=""):
            resp = MagicMock()
            if "note-taker" in prompt or "Summarize" in prompt:
                resp.content = "Nothing. []"
            else:
                resp.content = "NO"
            return resp

        monkeypatch.setattr(ct_mod, "tracked_ainvoke", quick_no)
        monkeypatch.setattr(ct_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(ct_mod, "get_employee_skills_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "get_employee_tools_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "_publish", AsyncMock())
        monkeypatch.setattr(ct_mod, "_chat", AsyncMock())

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Quick sync",
            "participant_ids": ["001", "002"],
        })

        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_meeting_evaluate_exception_filtered(self, monkeypatch):
        """Exceptions in evaluate are filtered out gracefully."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp1 = _make_emp("001", name="Alice", nickname="小A")
        emp2 = _make_emp("002", name="Bob", nickname="小B")
        cs.employees["001"] = emp1
        cs.employees["002"] = emp2
        room = MeetingRoom(
            id="r1", name="Room A", description="Small",
            capacity=6, is_booked=False,
        )
        cs.meeting_rooms["r1"] = room
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        call_idx = 0

        async def flaky_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                raise RuntimeError("Flaky LLM")
            resp = MagicMock()
            if "note-taker" in prompt or "Summarize" in prompt:
                resp.content = "Done. []"
            else:
                resp.content = "NO"
            return resp

        monkeypatch.setattr(ct_mod, "tracked_ainvoke", flaky_ainvoke)
        monkeypatch.setattr(ct_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(ct_mod, "get_employee_skills_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "get_employee_tools_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "_publish", AsyncMock())
        monkeypatch.setattr(ct_mod, "_chat", AsyncMock())

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Flaky test",
            "participant_ids": ["001", "002"],
            "initiator_id": "001",
        })

        assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# Additional coverage: build_speech_prompt with agenda
# ---------------------------------------------------------------------------

class TestBuildSpeechPromptWithAgenda:
    def test_speech_prompt_with_agenda(self):
        from onemancompany.agents.common_tools import _build_speech_prompt

        emp = _make_emp("001", name="Alice", nickname="A")

        with patch("onemancompany.agents.common_tools.get_employee_skills_prompt", return_value=""):
            with patch("onemancompany.agents.common_tools.get_employee_tools_prompt", return_value=""):
                prompt = _build_speech_prompt(emp, "Design review", "Review mockups and plan", [{"speaker": "Bob", "message": "Hi"}])

        assert "Design review" in prompt
        assert "Review mockups and plan" in prompt
        assert "Bob" in prompt


# ---------------------------------------------------------------------------
# Additional coverage: _build_employee_context without work_principles
# ---------------------------------------------------------------------------

class TestBuildEmployeeContextNoPrinciples:
    def test_no_principles(self):
        from onemancompany.agents.common_tools import _build_employee_context

        emp = _make_emp("001", name="Alice", nickname="A", work_principles="")

        with patch("onemancompany.agents.common_tools.get_employee_skills_prompt", return_value=""):
            with patch("onemancompany.agents.common_tools.get_employee_tools_prompt", return_value=""):
                ctx = _build_employee_context(emp)

        assert "Alice" in ctx
        assert "principles" not in ctx.lower()


# ---------------------------------------------------------------------------
# pull_meeting: initiator not in participant_ids (line 420)
# ---------------------------------------------------------------------------

class TestPullMeetingInitiatorNotInParticipants:
    @pytest.mark.asyncio
    async def test_initiator_added_to_speakers(self, monkeypatch):
        """Initiator not in participant_ids gets added to speakers list."""
        from onemancompany.agents import common_tools as ct_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp1 = _make_emp("001", name="Alice", nickname="小A")
        emp2 = _make_emp("002", name="Bob", nickname="小B")
        emp3 = _make_emp("003", name="Charlie", nickname="小C")
        cs.employees["001"] = emp1
        cs.employees["002"] = emp2
        cs.employees["003"] = emp3
        room = MeetingRoom(
            id="r1", name="Room A", description="Small",
            capacity=6, is_booked=False,
        )
        cs.meeting_rooms["r1"] = room
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        async def quick_end(llm, prompt, category="", employee_id=""):
            resp = MagicMock()
            if "note-taker" in prompt or "Summarize" in prompt:
                resp.content = "Done. []"
            else:
                resp.content = "NO"
            return resp

        monkeypatch.setattr(ct_mod, "tracked_ainvoke", quick_end)
        monkeypatch.setattr(ct_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(ct_mod, "get_employee_skills_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "get_employee_tools_prompt", lambda eid: "")
        monkeypatch.setattr(ct_mod, "_publish", AsyncMock())
        monkeypatch.setattr(ct_mod, "_chat", AsyncMock())

        result = await ct_mod.pull_meeting.ainvoke({
            "topic": "Sync",
            "participant_ids": ["001", "002"],
            "initiator_id": "003",  # Not in participant_ids
        })

        assert result["status"] == "completed"
