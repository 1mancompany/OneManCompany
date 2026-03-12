"""Unit tests for agents/termination.py — execute_fire flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from onemancompany.core.config import FOUNDING_LEVEL
from onemancompany.core import state as state_mod
from onemancompany.core.state import CompanyState, Employee


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cs():
    cs = CompanyState()
    cs._next_employee_number = 100
    return cs


def _make_emp(
    emp_id: str,
    level: int = 1,
    **kwargs,
) -> Employee:
    defaults = dict(
        id=emp_id, name=f"Emp {emp_id}", role="Engineer",
        skills=["python"], employee_number=emp_id, nickname="测试",
        level=level,
    )
    defaults.update(kwargs)
    return Employee(**defaults)


# ---------------------------------------------------------------------------
# execute_fire tests
# ---------------------------------------------------------------------------

class TestExecuteFire:
    @pytest.mark.asyncio
    async def test_fire_normal_employee(self, monkeypatch):
        from onemancompany.agents import termination as term_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=2)
        cs.employees["00010"] = emp
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        result = await term_mod.execute_fire("00010", reason="poor performance")

        assert result["status"] == "fired"
        assert result["name"] == "Emp 00010"
        assert "00010" not in cs.employees
        assert "00010" in cs.ex_employees
        assert cs.activity_log[-1]["type"] == "employee_fired"
        assert cs.activity_log[-1]["reason"] == "poor performance"

    @pytest.mark.asyncio
    async def test_cannot_fire_founding(self, monkeypatch):
        from onemancompany.agents import termination as term_mod

        cs = _make_cs()
        emp = _make_emp("00002", level=FOUNDING_LEVEL)
        cs.employees["00002"] = emp
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(state_mod, "company_state", cs)

        result = await term_mod.execute_fire("00002")

        assert "error" in result
        assert "founding" in result["error"].lower() or "Cannot" in result["error"]
        assert "00002" in cs.employees

    @pytest.mark.asyncio
    async def test_fire_nonexistent_employee(self, monkeypatch):
        from onemancompany.agents import termination as term_mod

        cs = _make_cs()
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(state_mod, "company_state", cs)

        result = await term_mod.execute_fire("99999")

        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_fire_stops_agent_loop(self, monkeypatch):
        from onemancompany.agents import termination as term_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=1)
        cs.employees["00010"] = emp
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        # Mock the employee_manager in the source module (agent_loop)
        mock_task = MagicMock()
        mock_manager = MagicMock()
        mock_manager._running_tasks = {"00010": mock_task}
        mock_manager.unregister = MagicMock()

        with patch("onemancompany.core.agent_loop.employee_manager", mock_manager):
            result = await term_mod.execute_fire("00010")

        assert result["status"] == "fired"
        mock_task.cancel.assert_called_once()
        mock_manager.unregister.assert_called_once_with("00010")

    @pytest.mark.asyncio
    async def test_fire_moves_to_ex_employees(self, monkeypatch):
        from onemancompany.agents import termination as term_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=1, name="Test Worker", nickname="剑客")
        cs.employees["00010"] = emp
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        result = await term_mod.execute_fire("00010")

        assert result["status"] == "fired"
        assert "00010" not in cs.employees
        ex = cs.ex_employees.get("00010")
        assert ex is not None
        assert ex.name == "Test Worker"

    @pytest.mark.asyncio
    async def test_fire_publishes_event(self, monkeypatch):
        from onemancompany.agents import termination as term_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=1)
        cs.employees["00010"] = emp
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10

        mock_bus = MagicMock(publish=AsyncMock())
        monkeypatch.setattr(term_mod, "event_bus", mock_bus)

        await term_mod.execute_fire("00010", reason="test reason")

        # Should have published employee_fired + state_snapshot
        assert mock_bus.publish.call_count == 2
        first_call = mock_bus.publish.call_args_list[0]
        event = first_call[0][0]
        assert event.type == "employee_fired"
        assert event.payload["id"] == "00010"
        assert event.payload["reason"] == "test reason"

    @pytest.mark.asyncio
    async def test_fire_agent_loop_exception_swallowed(self, monkeypatch):
        """Lines 56-57: Exception in agent_loop import/cancel is swallowed."""
        from onemancompany.agents import termination as term_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=1)
        cs.employees["00010"] = emp
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        # Make the employee_manager.unregister raise to trigger the except on line 56-57
        mock_manager = MagicMock()
        mock_manager._running_tasks = MagicMock()
        mock_manager._running_tasks.get.side_effect = RuntimeError("broken")

        with patch("onemancompany.core.agent_loop.employee_manager", mock_manager):
            result = await term_mod.execute_fire("00010")

        assert result["status"] == "fired"
        assert "00010" not in cs.employees

    @pytest.mark.asyncio
    async def test_fire_unregisters_custom_tools(self, tmp_path, monkeypatch):
        """Lines 65-79: Tool unregistration from manifest during fire."""
        import yaml
        from onemancompany.agents import termination as term_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=1)
        cs.employees["00010"] = emp
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))
        monkeypatch.setattr(term_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(term_mod, "TOOLS_DIR", tmp_path / "tools")

        # Create employee manifest with custom tools
        emp_tools_dir = tmp_path / "employees" / "00010" / "tools"
        emp_tools_dir.mkdir(parents=True)
        manifest = {"custom_tools": ["my_tool"]}
        (emp_tools_dir / "manifest.yaml").write_text(yaml.dump(manifest))

        # Create tool.yaml with source_talent and empty allowed_users
        tool_dir = tmp_path / "tools" / "my_tool"
        tool_dir.mkdir(parents=True)
        tool_data = {"source_talent": "test_talent", "allowed_users": []}
        (tool_dir / "tool.yaml").write_text(yaml.dump(tool_data))

        mock_unregister = MagicMock()
        with patch("onemancompany.agents.onboarding.unregister_tool_user", mock_unregister):
            result = await term_mod.execute_fire("00010")

        assert result["status"] == "fired"
        mock_unregister.assert_called_once_with("my_tool", "00010")
        # Orphaned tool should have been removed
        assert not tool_dir.exists()

    @pytest.mark.asyncio
    async def test_fire_tool_unregistration_exception_swallowed(self, monkeypatch):
        """Lines 78-79: Exception in tool unregistration is swallowed."""
        from onemancompany.agents import termination as term_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=1)
        cs.employees["00010"] = emp
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        # Make onboarding import fail — triggers exception on line 78
        with patch.dict("sys.modules", {"onemancompany.agents.onboarding": None}):
            result = await term_mod.execute_fire("00010")

        assert result["status"] == "fired"
