"""Tests for agent_family switching — _create_executor_for_family, switch_agent_family."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.vessel import (
    LangChainExecutor,
    ClaudeSessionExecutor,
    _create_executor_for_family,
)


class TestCreateExecutorForFamily:
    def test_langchain_with_agent_cls(self):
        mock_cls = MagicMock
        executor = _create_executor_for_family("langchain", "00002", mock_cls, "/tmp")
        assert isinstance(executor, LangChainExecutor)

    def test_langchain_without_agent_cls(self):
        with patch("onemancompany.agents.base.EmployeeAgent", MagicMock):
            executor = _create_executor_for_family("langchain", "00010", None, "/tmp")
        assert isinstance(executor, LangChainExecutor)

    def test_claude(self):
        executor = _create_executor_for_family("claude", "00002", MagicMock, "/tmp")
        assert isinstance(executor, ClaudeSessionExecutor)

    def test_openclaw(self):
        from pathlib import Path
        from onemancompany.core.subprocess_executor import SubprocessExecutor
        executor = _create_executor_for_family("openclaw", "00002", MagicMock, Path("/tmp"))
        assert isinstance(executor, SubprocessExecutor)

    def test_unknown_defaults_to_langchain(self):
        executor = _create_executor_for_family("unknown", "00002", MagicMock, "/tmp")
        assert isinstance(executor, LangChainExecutor)


class TestSwitchAgentFamily:
    @pytest.mark.asyncio
    async def test_switch_idle_employee(self):
        from onemancompany.core.vessel import switch_agent_family, employee_manager

        # Register a mock employee first
        mock_executor = MagicMock()
        employee_manager.register("99999", mock_executor)

        mock_cfg = MagicMock()
        mock_cfg.agent_family = "langchain"

        with patch("onemancompany.core.config.employee_configs", {"99999": mock_cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", MagicMock()):
            result = await switch_agent_family("99999", "claude")

        assert result == "ClaudeSessionExecutor"
        assert isinstance(employee_manager.executors["99999"], ClaudeSessionExecutor)
        assert mock_cfg.agent_family == "claude"

        # Cleanup
        employee_manager.unregister("99999")

    @pytest.mark.asyncio
    async def test_switch_busy_employee_raises(self):
        from onemancompany.core.vessel import switch_agent_family, employee_manager

        employee_manager._running_tasks["99998"] = MagicMock()

        with pytest.raises(RuntimeError, match="currently running"):
            await switch_agent_family("99998", "claude")

        # Cleanup
        employee_manager._running_tasks.pop("99998", None)

    @pytest.mark.asyncio
    async def test_switch_system_task_running_raises(self):
        from onemancompany.core.vessel import switch_agent_family, employee_manager

        employee_manager.register("99997", MagicMock())
        employee_manager._system_tasks["99997"] = MagicMock()

        with pytest.raises(RuntimeError, match="system task"):
            await switch_agent_family("99997", "claude")

        # Cleanup
        employee_manager._system_tasks.pop("99997", None)
        employee_manager.unregister("99997")

    @pytest.mark.asyncio
    async def test_switch_invalid_family_raises(self):
        from onemancompany.core.vessel import switch_agent_family

        with pytest.raises(ValueError, match="Invalid agent_family"):
            await switch_agent_family("99999", "invalid_family")
