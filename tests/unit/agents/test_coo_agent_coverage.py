"""Coverage tests for agents/coo_agent.py — missing lines."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _load_assets_from_disk (lines 40-66)
# ---------------------------------------------------------------------------

class TestLoadAssetsFromDisk:
    def test_loads_tools_and_rooms(self, monkeypatch):
        import onemancompany.agents.coo_agent as coo_mod
        from onemancompany.core.state import company_state

        mock_tools = {
            "tool1": {
                "name": "My Tool",
                "description": "A tool",
                "added_by": "COO",
                "sprite": "desk_equipment",
                "_folder_name": "my_tool",
                "_files": ["tool.yaml", "icon.png"],
                "tool_type": "template",
            }
        }
        mock_rooms = {
            "room1": {
                "name": "Room 1",
                "description": "A room",
                "capacity": 4,
                "position": [1, 2],
            }
        }
        # Clean state
        company_state.tools.clear()
        company_state.meeting_rooms.clear()

        with patch("onemancompany.agents.coo_agent.load_assets", return_value=(mock_tools, mock_rooms)):
            coo_mod._load_assets_from_disk()

        assert "tool1" in company_state.tools
        assert company_state.tools["tool1"].has_icon is True
        assert "room1" in company_state.meeting_rooms
        # Cleanup
        company_state.tools.pop("tool1", None)
        company_state.meeting_rooms.pop("room1", None)


# ---------------------------------------------------------------------------
# register_asset — validation branches (lines 151, 158, 162-176, 178-179)
# ---------------------------------------------------------------------------

class TestRegisterAssetValidation:
    def test_reject_reference_keyword(self, monkeypatch):
        from onemancompany.agents.coo_agent import register_asset
        result = register_asset.invoke({
            "name": "Reference Code Collection",
            "description": "A collection",
            "tool_type": "template",
        })
        assert result["status"] == "error"
        assert "reference code" in result["message"].lower()

    def test_reject_duplicate(self, monkeypatch):
        """Cover line 158: duplicate check. Note: source iterates dict values."""
        from onemancompany.agents.coo_agent import register_asset
        from onemancompany.core.state import company_state
        # Patch company_state.tools to be a list-like that yields objects with .name
        mock_tool = MagicMock()
        mock_tool.name = "My Unique Tool"
        original_tools = company_state.tools
        # Use a dict that when iterated gives values with .name attr
        class ToolDict(dict):
            def __iter__(self):
                return iter(self.values())
        td = ToolDict()
        td["existing"] = mock_tool
        company_state.tools = td
        try:
            result = register_asset.invoke({
                "name": "My Unique Tool",
                "description": "Another one",
                "tool_type": "template",
            })
            assert result["status"] == "error"
            assert "already exists" in result["message"]
        finally:
            company_state.tools = original_tools

    def test_script_type_no_files(self):
        from onemancompany.agents.coo_agent import register_asset
        result = register_asset.invoke({
            "name": "Script Tool",
            "description": "A script",
            "tool_type": "script",
            "source_files": [],
        })
        assert result["status"] == "error"
        assert "source_files" in result["message"]

    def test_script_type_no_executable(self):
        from onemancompany.agents.coo_agent import register_asset
        result = register_asset.invoke({
            "name": "Script Tool",
            "description": "A script",
            "tool_type": "script",
            "source_files": ["readme.md"],
        })
        assert result["status"] == "error"
        assert ".py or .sh" in result["message"]

    def test_script_type_syntax_error(self, tmp_path):
        from onemancompany.agents.coo_agent import register_asset
        (tmp_path / "bad.py").write_text("def f(\n")
        result = register_asset.invoke({
            "name": "Script Tool",
            "description": "A script",
            "tool_type": "script",
            "source_files": ["bad.py"],
            "source_project_dir": str(tmp_path),
        })
        assert result["status"] == "error"
        assert "syntax error" in result["message"].lower()

    def test_reference_type_no_url(self):
        from onemancompany.agents.coo_agent import register_asset
        result = register_asset.invoke({
            "name": "Ref Tool",
            "description": "A ref",
            "tool_type": "reference",
        })
        assert result["status"] == "error"
        assert "reference_url" in result["message"]


# ---------------------------------------------------------------------------
# request_hiring (lines 592-636)
# ---------------------------------------------------------------------------

class TestRequestHiringFull:
    def test_request_with_project_context(self, monkeypatch):
        from onemancompany.agents.coo_agent import request_hiring, pending_hiring_requests
        import onemancompany.agents.coo_agent as coo_mod

        mock_vessel = MagicMock()
        mock_task = MagicMock()
        mock_task.project_id = "proj1"
        mock_task.original_project_id = ""
        mock_task.project_dir = "/tmp/proj"
        mock_task.original_project_dir = ""
        mock_vessel.get_task.return_value = mock_task
        mock_vessel.employee_id = "00003"

        mock_dispatch = MagicMock(return_value={"status": "dispatched", "node_id": "n1"})
        mock_manager = MagicMock()
        mock_manager._event_loop = None

        with patch("onemancompany.core.agent_loop._current_vessel") as mock_cv, \
             patch("onemancompany.core.agent_loop._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools.dispatch_child", MagicMock(invoke=mock_dispatch)), \
             patch("onemancompany.core.vessel.employee_manager", mock_manager), \
             patch("onemancompany.agents.coo_agent.event_bus") as mock_bus, \
             patch("onemancompany.api.routes._pending_coo_hire_queue", []):
            mock_cv.get.return_value = mock_vessel
            mock_tid.get.return_value = "tid1"
            mock_bus.publish.return_value = AsyncMock()()

            result = request_hiring.invoke({
                "role": "Designer",
                "department": "Design",
                "reason": "Need a designer",
            })
        assert result["status"] == "auto_approved"
        assert result["hire_id"] in pending_hiring_requests
        # Cleanup
        pending_hiring_requests.pop(result["hire_id"], None)

    def test_request_fallback_adhoc(self, monkeypatch):
        """Cover line 635-641: dispatch_child fails, falls back to adhoc."""
        from onemancompany.agents.coo_agent import request_hiring, pending_hiring_requests

        mock_dispatch = MagicMock(return_value={"status": "error", "message": "no context"})
        mock_manager = MagicMock()
        mock_manager._event_loop = None

        with patch("onemancompany.core.agent_loop._current_vessel") as mock_cv, \
             patch("onemancompany.core.agent_loop._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools.dispatch_child", MagicMock(invoke=mock_dispatch)), \
             patch("onemancompany.core.vessel.employee_manager", mock_manager), \
             patch("onemancompany.agents.coo_agent.event_bus") as mock_bus, \
             patch("onemancompany.api.routes._push_adhoc_task") as mock_push, \
             patch("onemancompany.api.routes._pending_coo_hire_queue", []):
            mock_cv.get.return_value = MagicMock(get_task=MagicMock(return_value=None))
            mock_tid.get.return_value = "tid1"
            mock_bus.publish.return_value = AsyncMock()()

            result = request_hiring.invoke({
                "role": "Dev",
                "department": "Tech",
                "reason": "Need dev",
            })
        assert result["status"] == "auto_approved"
        mock_push.assert_called_once()
        pending_hiring_requests.pop(result["hire_id"], None)


# ---------------------------------------------------------------------------
# deposit_company_knowledge (lines 712-735)
# ---------------------------------------------------------------------------

class TestDepositCompanyKnowledge:
    def test_invalid_category(self):
        from onemancompany.agents.coo_agent import deposit_company_knowledge
        result = deposit_company_knowledge.invoke({
            "category": "invalid",
            "name": "test",
            "content": "test content",
        })
        assert result["status"] == "error"

    def test_workflow_validation_error(self):
        from onemancompany.agents.coo_agent import deposit_company_knowledge
        from onemancompany.agents.coo_agent import WorkflowValidationError
        with patch("onemancompany.agents.coo_agent.save_workflow", side_effect=WorkflowValidationError("bad")), \
             patch("onemancompany.agents.coo_agent._append_activity"):
            result = deposit_company_knowledge.invoke({
                "category": "workflow",
                "name": "bad_workflow",
                "content": "bad content",
            })
        assert result["status"] == "error"

    def test_culture_deposit(self):
        from onemancompany.agents.coo_agent import deposit_company_knowledge
        with patch("onemancompany.core.store.load_culture", return_value=[]), \
             patch("onemancompany.core.store.save_culture", new_callable=AsyncMock), \
             patch("onemancompany.agents.coo_agent._append_activity"):
            result = deposit_company_knowledge.invoke({
                "category": "culture",
                "name": "value1",
                "content": "We value teamwork",
            })
        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# assign_department (lines 775-862)
# ---------------------------------------------------------------------------

class TestAssignDepartmentAndRole:
    @pytest.mark.asyncio
    async def test_no_change(self):
        from onemancompany.agents.coo_agent import assign_department
        with patch("onemancompany.core.store.load_employee", return_value={
            "department": "Tech",
            "role": "Dev",
        }):
            result = await assign_department.ainvoke({
                "target_employee_id": "00010",
                "department": "Tech",
                "role": "Dev",
            })
        assert result["status"] == "no_change"

    @pytest.mark.asyncio
    async def test_employee_not_found(self):
        from onemancompany.agents.coo_agent import assign_department
        with patch("onemancompany.core.store.load_employee", return_value=None):
            result = await assign_department.ainvoke({
                "target_employee_id": "99999",
                "department": "Tech",
            })
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_dept_change(self):
        from onemancompany.agents.coo_agent import assign_department
        with patch("onemancompany.core.store.load_employee", return_value={
            "department": "General",
            "role": "Dev",
            "name": "Test",
            "remote": False,
        }), \
             patch("onemancompany.core.store.save_employee", new_callable=AsyncMock), \
             patch("onemancompany.core.layout.get_next_desk_for_department", return_value=(5, 8)), \
             patch("onemancompany.core.layout.compute_layout"), \
             patch("onemancompany.agents.coo_agent._append_activity"), \
             patch("onemancompany.agents.coo_agent.event_bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            result = await assign_department.ainvoke({
                "target_employee_id": "00010",
                "department": "Tech",
                "role": "Senior Dev",
            })
        assert result["status"] == "ok"
        assert result["department"] == "Tech"


# ---------------------------------------------------------------------------
# COOAgent._get_role_identity_section (line 900)
# ---------------------------------------------------------------------------

class TestCOOAgentRoleIdentity:
    def test_no_role_guide(self, tmp_path, monkeypatch):
        from onemancompany.agents.coo_agent import COOAgent
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00003").mkdir()
        agent = COOAgent.__new__(COOAgent)
        agent.employee_id = "00003"
        result = agent._get_role_identity_section()
        assert result == ""
