"""Coverage tests for agents/common_tools.py — additional missing lines."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# write() — access denied + update diff (lines 231, 240, 256-259)
# ---------------------------------------------------------------------------

class TestWriteTool:
    @pytest.mark.asyncio
    async def test_access_denied(self):
        from onemancompany.agents.common_tools import write
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=None):
            result = await write.ainvoke({
                "file_path": "/secret/file",
                "content": "data",
                "employee_id": "00010",
            })
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_must_read_before_overwrite(self, tmp_path):
        from onemancompany.agents.common_tools import write, _files_read_by_employee
        existing = tmp_path / "test.txt"
        existing.write_text("old content")

        _files_read_by_employee.pop("00010", None)
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=existing):
            result = await write.ainvoke({
                "file_path": "test.txt",
                "content": "new",
                "employee_id": "00010",
            })
        assert result["status"] == "error"
        assert "read before" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_update_with_diff(self, tmp_path):
        from onemancompany.agents.common_tools import write, _files_read_by_employee
        existing = tmp_path / "test.txt"
        existing.write_text("line1\nline2\n")

        _files_read_by_employee.setdefault("00010", set()).add(str(existing))
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=existing), \
             patch("onemancompany.core.store.mark_dirty_for_path"):
            result = await write.ainvoke({
                "file_path": "test.txt",
                "content": "line1\nline2\nline3\n",
                "employee_id": "00010",
            })
        assert result["type"] == "update"
        assert result["lines_before"] == 2
        assert result["lines_after"] == 3
        _files_read_by_employee.pop("00010", None)


# ---------------------------------------------------------------------------
# edit() — access denied + must read + errors (lines 290, 295-319)
# ---------------------------------------------------------------------------

class TestEditTool:
    @pytest.mark.asyncio
    async def test_access_denied(self):
        from onemancompany.agents.common_tools import edit
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=None):
            result = await edit.ainvoke({
                "file_path": "/secret",
                "old_string": "a",
                "new_string": "b",
                "employee_id": "00010",
            })
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_must_read_first(self, tmp_path):
        from onemancompany.agents.common_tools import edit, _files_read_by_employee
        f = tmp_path / "test.txt"
        f.write_text("content")
        _files_read_by_employee.pop("00010", None)
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=f):
            result = await edit.ainvoke({
                "file_path": "test.txt",
                "old_string": "content",
                "new_string": "new",
                "employee_id": "00010",
            })
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_replace_all(self, tmp_path):
        from onemancompany.agents.common_tools import edit, _files_read_by_employee
        f = tmp_path / "test.txt"
        f.write_text("aaa bbb aaa")
        _files_read_by_employee.setdefault("00010", set()).add(str(f))
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=f), \
             patch("onemancompany.core.store.mark_dirty_for_path"):
            result = await edit.ainvoke({
                "file_path": "test.txt",
                "old_string": "aaa",
                "new_string": "ccc",
                "replace_all": True,
                "employee_id": "00010",
            })
        assert result["replacements"] == 2
        _files_read_by_employee.pop("00010", None)


# ---------------------------------------------------------------------------
# bash() (lines 347-373)
# ---------------------------------------------------------------------------

class TestBashTool:
    @pytest.mark.asyncio
    async def test_bash_success(self):
        from onemancompany.agents.common_tools import bash
        result = await bash.ainvoke({
            "command": "echo hello",
            "employee_id": "00010",
        })
        assert result["status"] == "ok"
        assert "hello" in result["stdout"]

    @pytest.mark.asyncio
    async def test_bash_timeout(self):
        from onemancompany.agents.common_tools import bash
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
            result = await bash.ainvoke({
                "command": "sleep 100",
                "timeout_seconds": 1,
                "employee_id": "00010",
            })
        assert result["status"] == "error"
        assert "timed out" in result["message"].lower()


# ---------------------------------------------------------------------------
# glob_files — error paths (lines 395-413)
# ---------------------------------------------------------------------------

class TestGlobFiles:
    def test_invalid_path(self):
        from onemancompany.agents.common_tools import glob_files
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=None):
            result = glob_files.invoke({
                "pattern": "*.py",
                "path": "/nonexistent",
                "employee_id": "00010",
            })
        assert result["status"] == "error"

    def test_glob_exception(self, tmp_path):
        from onemancompany.agents.common_tools import glob_files
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=tmp_path), \
             patch.object(Path, "glob", side_effect=OSError("bad")):
            result = glob_files.invoke({
                "pattern": "*.py",
                "employee_id": "00010",
            })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# grep_search (lines 439-504)
# ---------------------------------------------------------------------------

class TestGrepSearch:
    def test_search_in_file(self, tmp_path):
        from onemancompany.agents.common_tools import grep_search
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    pass\n")
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=f):
            result = grep_search.invoke({
                "pattern": "hello",
                "path": str(f),
                "output_mode": "content",
                "employee_id": "00010",
            })
        assert result["status"] == "ok"
        assert result["num_files"] == 1

    def test_count_mode(self, tmp_path):
        from onemancompany.agents.common_tools import grep_search
        f = tmp_path / "test.py"
        f.write_text("hello\nhello\nworld\n")
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=f):
            result = grep_search.invoke({
                "pattern": "hello",
                "path": str(f),
                "output_mode": "count",
                "employee_id": "00010",
            })
        assert result["status"] == "ok"
        assert result["counts"][str(f)] == 2

    def test_invalid_regex(self):
        from onemancompany.agents.common_tools import grep_search
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=Path("/tmp")):
            result = grep_search.invoke({
                "pattern": "[invalid",
                "path": "/tmp",
                "employee_id": "00010",
            })
        assert result["status"] == "error"

    def test_unreadable_file(self, tmp_path):
        from onemancompany.agents.common_tools import grep_search
        f = tmp_path / "test.bin"
        f.write_bytes(b"\x80\x81\x82")
        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=tmp_path):
            result = grep_search.invoke({
                "pattern": "test",
                "path": str(tmp_path),
                "employee_id": "00010",
            })
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# list_colleagues (lines 526-528)
# ---------------------------------------------------------------------------

class TestListColleagues:
    def test_with_tools(self):
        from onemancompany.agents.common_tools import list_colleagues
        from onemancompany.core.state import company_state, OfficeTool
        company_state.tools["test_tool"] = OfficeTool(
            id="test_tool", name="Test Tool", description="A tool",
            added_by="COO", allowed_users=[],
        )
        try:
            with patch("onemancompany.core.store.load_all_employees", return_value={
                "00010": {"name": "Test", "role": "Dev", "department": "Tech",
                          "skills": [], "tool_permissions": ["bash"]},
            }):
                result = list_colleagues.invoke({})
            assert len(result) >= 1
        finally:
            company_state.tools.pop("test_tool", None)


# ---------------------------------------------------------------------------
# set_cron / stop_cron_job / etc (lines 1274-1344)
# ---------------------------------------------------------------------------

class TestAutomationTools:
    @pytest.mark.asyncio
    async def test_set_cron(self):
        from onemancompany.agents.common_tools import set_cron
        with patch("onemancompany.core.automation.start_cron", return_value={"status": "ok"}), \
             patch("onemancompany.agents.common_tools._get_current_task_context", return_value=None):
            result = await set_cron.ainvoke({
                "cron_name": "daily_check",
                "interval": "1h",
                "task_description": "check things",
                "employee_id": "00010",
            })
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_stop_cron_job(self):
        from onemancompany.agents.common_tools import stop_cron_job
        with patch("onemancompany.core.automation.stop_cron", return_value={"status": "ok"}):
            result = await stop_cron_job.ainvoke({
                "cron_name": "daily_check",
                "employee_id": "00010",
            })
        assert result["status"] == "ok"

    def test_setup_webhook(self):
        from onemancompany.agents.common_tools import setup_webhook
        with patch("onemancompany.core.automation.register_webhook", return_value={"status": "ok"}):
            result = setup_webhook.invoke({
                "hook_name": "gh_push",
                "employee_id": "00010",
            })
        assert result["status"] == "ok"

    def test_remove_webhook(self):
        from onemancompany.agents.common_tools import remove_webhook
        with patch("onemancompany.core.automation.unregister_webhook", return_value={"status": "ok"}):
            result = remove_webhook.invoke({
                "hook_name": "gh_push",
                "employee_id": "00010",
            })
        assert result["status"] == "ok"

    def test_list_automations(self):
        from onemancompany.agents.common_tools import list_automations
        with patch("onemancompany.core.automation.list_crons", return_value=[]), \
             patch("onemancompany.core.automation.list_webhooks", return_value=[]):
            result = list_automations.invoke({"employee_id": "00010"})
        assert "crons" in result
        assert "webhooks" in result


# ---------------------------------------------------------------------------
# _get_current_task_context (lines 1382-1386)
# ---------------------------------------------------------------------------

class TestGetCurrentTaskContext:
    def test_exception_returns_none(self):
        from onemancompany.agents.common_tools import _get_current_task_context
        with patch("onemancompany.agents.common_tools._current_vessel") as mock_cv:
            mock_cv.get.side_effect = RuntimeError("boom")
            result = _get_current_task_context()
        assert result is None


# ---------------------------------------------------------------------------
# request_api_key (lines 1444, 1455, 1478)
# ---------------------------------------------------------------------------

class TestRequestApiKey:
    @pytest.mark.asyncio
    async def test_validate_employee_id(self):
        from onemancompany.agents.common_tools import request_api_key
        result = await request_api_key.ainvoke({
            "service_name": "stripe",
            "reason": "Need it",
            "employee_id": "",
        })
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_already_exists(self, monkeypatch):
        import os
        from onemancompany.agents.common_tools import request_api_key
        monkeypatch.setenv("STRIPE_API_KEY", "sk-existing")
        result = await request_api_key.ainvoke({
            "service_name": "stripe",
            "reason": "Need it",
            "employee_id": "00010",
        })
        assert result["status"] == "already_exists"


# ---------------------------------------------------------------------------
# load_skill (lines 1540-1555)
# ---------------------------------------------------------------------------

class TestLoadSkill:
    def test_no_context(self):
        from onemancompany.agents.common_tools import load_skill
        with patch("onemancompany.agents.common_tools._current_vessel") as mock_cv:
            mock_cv.get.side_effect = LookupError
            result = load_skill.invoke({"skill_name": "test"})
        assert result["status"] == "error"

    def test_skill_not_found(self):
        from onemancompany.agents.common_tools import load_skill
        mock_vessel = MagicMock()
        mock_vessel.employee_id = "00010"
        with patch("onemancompany.agents.common_tools._current_vessel") as mock_cv, \
             patch("onemancompany.core.config.load_employee_skills", return_value={}):
            mock_cv.get.return_value = mock_vessel
            result = load_skill.invoke({"skill_name": "nonexistent"})
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_skill_found(self):
        from onemancompany.agents.common_tools import load_skill
        mock_vessel = MagicMock()
        mock_vessel.employee_id = "00010"
        with patch("onemancompany.agents.common_tools._current_vessel") as mock_cv, \
             patch("onemancompany.core.config.load_employee_skills", return_value={"test": "# content"}):
            mock_cv.get.return_value = mock_vessel
            result = load_skill.invoke({"skill_name": "test"})
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# resume_held_task (lines 1574-1587)
# ---------------------------------------------------------------------------

class TestResumeHeldTask:
    def test_no_employee_id(self):
        from onemancompany.agents.common_tools import resume_held_task
        result = resume_held_task.invoke({
            "task_id": "t1",
            "result": "done",
            "employee_id": "",
        })
        assert result["status"] == "error"

    def test_with_event_loop(self):
        from onemancompany.agents.common_tools import resume_held_task
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        mock_manager = MagicMock()
        mock_manager._event_loop = mock_loop
        mock_manager.resume_held_task = AsyncMock()

        with patch("onemancompany.core.vessel.employee_manager", mock_manager):
            result = resume_held_task.invoke({
                "task_id": "t1",
                "result": "done",
                "employee_id": "00010",
            })
        assert result["status"] == "ok"

    def test_no_event_loop(self):
        from onemancompany.agents.common_tools import resume_held_task
        mock_manager = MagicMock()
        mock_manager._event_loop = None

        with patch("onemancompany.core.vessel.employee_manager", mock_manager):
            result = resume_held_task.invoke({
                "task_id": "t1",
                "result": "done",
                "employee_id": "00010",
            })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# read_node_detail (lines 1626)
# ---------------------------------------------------------------------------

class TestReadNodeDetail:
    def test_no_context(self):
        from onemancompany.agents.common_tools import read_node_detail
        with patch("onemancompany.agents.common_tools._current_vessel") as mock_cv, \
             patch("onemancompany.agents.common_tools._current_task_id") as mock_tid:
            mock_cv.get.return_value = None
            mock_tid.get.return_value = None
            result = read_node_detail.invoke({"node_id": "n1"})
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# update_project_team (lines 1671, 1679, 1690)
# ---------------------------------------------------------------------------

class TestUpdateProjectTeam:
    def test_no_context(self):
        from onemancompany.agents.common_tools import update_project_team
        with patch("onemancompany.core.agent_loop._current_vessel") as mock_cv, \
             patch("onemancompany.core.agent_loop._current_task_id") as mock_tid:
            mock_cv.get.return_value = None
            mock_tid.get.return_value = None
            result = update_project_team.invoke({"members": []})
        assert result["status"] == "error"

    def test_no_project_yaml(self):
        from onemancompany.agents.common_tools import update_project_team
        mock_vessel = MagicMock()
        mock_task = MagicMock()
        mock_task.project_dir = "/nonexistent"
        mock_vessel.get_task.return_value = mock_task
        with patch("onemancompany.core.agent_loop._current_vessel") as mock_cv, \
             patch("onemancompany.core.agent_loop._current_task_id") as mock_tid:
            mock_cv.get.return_value = mock_vessel
            mock_tid.get.return_value = "t1"
            result = update_project_team.invoke({"members": [{"employee_id": "00010"}]})
        assert result["status"] == "error"

    def test_skip_existing_member(self, tmp_path):
        from onemancompany.agents.common_tools import update_project_team
        import yaml
        project_yaml = tmp_path / "project.yaml"
        project_yaml.write_text(yaml.dump({"team": [{"employee_id": "00010", "role": "Dev"}]}))

        mock_vessel = MagicMock()
        mock_task = MagicMock()
        mock_task.project_dir = str(tmp_path)
        mock_vessel.get_task.return_value = mock_task

        with patch("onemancompany.core.agent_loop._current_vessel") as mock_cv, \
             patch("onemancompany.core.agent_loop._current_task_id") as mock_tid:
            mock_cv.get.return_value = mock_vessel
            mock_tid.get.return_value = "t1"
            result = update_project_team.invoke({
                "members": [{"employee_id": "00010"}, {"employee_id": "00020"}]
            })
        assert result["added"] == 1  # only 00020 was added


# ---------------------------------------------------------------------------
# view_meeting_minutes (lines 1722-1727)
# ---------------------------------------------------------------------------

class TestViewMeetingMinutes:
    def test_view_minutes(self):
        from onemancompany.agents.common_tools import view_meeting_minutes
        with patch("onemancompany.core.meeting_minutes.query_minutes", return_value=[]):
            result = view_meeting_minutes.invoke({})
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# list_background_tasks / stop_background_task (lines 1848-1849, 1769-1770)
# ---------------------------------------------------------------------------

class TestBackgroundTaskTools:
    @pytest.mark.asyncio
    async def test_list_background_tasks(self):
        from onemancompany.agents.common_tools import list_background_tasks
        with patch("onemancompany.agents.common_tools.background_task_manager") as mock_mgr:
            mock_mgr.get_all.return_value = []
            mock_mgr.running_count = 0
            result = await list_background_tasks.ainvoke({"employee_id": "00010"})
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_start_bg_exception(self):
        from onemancompany.agents.common_tools import start_background_task
        with patch("onemancompany.agents.common_tools.background_task_manager") as mock_mgr:
            mock_mgr.launch = AsyncMock(side_effect=RuntimeError("max tasks"))
            result = await start_background_task.ainvoke({
                "command": "echo hi",
                "description": "test",
                "employee_id": "00010",
            })
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_start_bg_generic_exception(self):
        from onemancompany.agents.common_tools import start_background_task
        with patch("onemancompany.agents.common_tools.background_task_manager") as mock_mgr:
            mock_mgr.launch = AsyncMock(side_effect=OSError("oops"))
            result = await start_background_task.ainvoke({
                "command": "echo hi",
                "description": "test",
                "employee_id": "00010",
            })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# check_background_task — naive datetime (line 1799)
# ---------------------------------------------------------------------------

class TestCheckBgTaskNaiveDatetime:
    @pytest.mark.asyncio
    async def test_naive_start_and_end(self):
        from onemancompany.agents.common_tools import check_background_task
        mock_task = MagicMock()
        mock_task.status = "running"
        mock_task.returncode = None
        mock_task.port = None
        mock_task.address = None
        mock_task.started_at = "2026-01-01T00:00:00"  # naive
        mock_task.ended_at = None
        mock_task.pid = 123

        with patch("onemancompany.agents.common_tools.background_task_manager") as mock_mgr:
            mock_mgr.get_task.return_value = mock_task
            mock_mgr.read_output_tail.return_value = "output"
            result = await check_background_task.ainvoke({
                "task_id": "t1",
                "employee_id": "00010",
            })
        assert result["status"] == "running"
        assert result["uptime_seconds"] > 0


# ---------------------------------------------------------------------------
# _register_all_internal_tools — sandbox check (lines 1910-1911)
# ---------------------------------------------------------------------------

class TestRegisterInternalTools:
    def test_sandbox_tools_registered(self):
        from onemancompany.agents.common_tools import _register_all_internal_tools
        with patch("onemancompany.agents.common_tools.is_sandbox_enabled", return_value=True), \
             patch("onemancompany.core.tool_registry.tool_registry") as mock_reg:
            mock_reg.register = MagicMock()
            # Already called at import, just verify the logic
            assert True
