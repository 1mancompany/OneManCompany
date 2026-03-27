"""Tests for hosting-based executor switching — _create_executor_for_hosting, switch_hosting."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.vessel import (
    LangChainExecutor,
    ClaudeSessionExecutor,
    _create_executor_for_hosting,
)


class TestCreateExecutorForHosting:
    def test_company_with_agent_cls(self):
        mock_cls = MagicMock
        executor = _create_executor_for_hosting("company", "00002", mock_cls, Path("/tmp"))
        assert isinstance(executor, LangChainExecutor)

    def test_company_without_agent_cls(self):
        with patch("onemancompany.agents.base.EmployeeAgent", MagicMock):
            executor = _create_executor_for_hosting("company", "00010", None, Path("/tmp"))
        assert isinstance(executor, LangChainExecutor)

    def test_self(self):
        executor = _create_executor_for_hosting("self", "00002", MagicMock, Path("/tmp"))
        assert isinstance(executor, ClaudeSessionExecutor)

    def test_openclaw(self):
        from onemancompany.core.subprocess_executor import SubprocessExecutor
        executor = _create_executor_for_hosting("openclaw", "00002", MagicMock, Path("/tmp"))
        assert isinstance(executor, SubprocessExecutor)

    def test_unknown_defaults_to_langchain(self):
        executor = _create_executor_for_hosting("unknown", "00002", MagicMock, Path("/tmp"))
        assert isinstance(executor, LangChainExecutor)


class TestSwitchHosting:
    @pytest.mark.asyncio
    async def test_switch_idle_employee(self):
        from onemancompany.core.vessel import switch_hosting, employee_manager

        mock_executor = MagicMock()
        employee_manager.register("99999", mock_executor)

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.core.config.employee_configs", {"99999": mock_cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", MagicMock()):
            result = await switch_hosting("99999", "self")

        assert result == "ClaudeSessionExecutor"
        assert isinstance(employee_manager.executors["99999"], ClaudeSessionExecutor)
        assert mock_cfg.hosting == "self"

        employee_manager.unregister("99999")

    @pytest.mark.asyncio
    async def test_switch_busy_employee_raises(self):
        from onemancompany.core.vessel import switch_hosting, employee_manager

        employee_manager._running_tasks["99998"] = MagicMock()

        with pytest.raises(RuntimeError, match="currently running"):
            await switch_hosting("99998", "self")

        employee_manager._running_tasks.pop("99998", None)

    @pytest.mark.asyncio
    async def test_switch_system_task_running_raises(self):
        from onemancompany.core.vessel import switch_hosting, employee_manager

        employee_manager.register("99997", MagicMock())
        employee_manager._system_tasks["99997"] = MagicMock()

        with pytest.raises(RuntimeError, match="system task"):
            await switch_hosting("99997", "self")

        employee_manager._system_tasks.pop("99997", None)
        employee_manager.unregister("99997")

    @pytest.mark.asyncio
    async def test_switch_invalid_hosting_raises(self):
        from onemancompany.core.vessel import switch_hosting

        with pytest.raises(ValueError, match="Invalid hosting"):
            await switch_hosting("99999", "invalid_value")
