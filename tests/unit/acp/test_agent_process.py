"""Tests for OMCAgent ACP agent process."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required env vars for OMCAgent construction."""
    monkeypatch.setenv("OMC_EMPLOYEE_ID", "test-emp-001")
    monkeypatch.setenv("OMC_EXECUTOR_TYPE", "langchain")
    monkeypatch.setenv("OMC_SERVER_URL", "http://localhost:8000")
    monkeypatch.setenv("OMC_EMPLOYEE_DIR", "/tmp/test_emp")


def _make_mock_backend() -> MagicMock:
    """Create a minimal mock ACP backend."""
    backend = MagicMock()
    backend.executor_type = "langchain"
    backend.execute = AsyncMock(return_value={"stop_reason": "end_turn", "usage": None})
    backend.set_model = AsyncMock()
    backend.set_config = AsyncMock()
    return backend


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInitializeReturnsCapabilities:
    """test_initialize_returns_capabilities"""

    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OMCAgent.initialize() should return a response with agent_info.name set."""
        _make_env(monkeypatch)

        from onemancompany.acp.agent_process import OMCAgent

        agent = OMCAgent()

        # Patch _init_backend so it doesn't try to import missing backend modules
        mock_backend = _make_mock_backend()
        with patch.object(agent, "_init_backend", AsyncMock(return_value=mock_backend)):
            response = await agent.initialize(protocol_version=1)

        assert response is not None
        assert response.agent_info is not None
        assert response.agent_info.name == "test-emp-001"


class TestNewSessionReturnsEmployeeId:
    """test_new_session_returns_employee_id"""

    @pytest.mark.asyncio
    async def test_new_session_returns_employee_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OMCAgent.new_session() should return session_id equal to employee_id."""
        _make_env(monkeypatch)

        from onemancompany.acp.agent_process import OMCAgent

        agent = OMCAgent()
        mock_backend = _make_mock_backend()
        agent._backend = mock_backend  # inject backend directly

        response = await agent.new_session(cwd="/tmp")

        assert response.session_id == "test-emp-001"


class TestPromptDispatchesToBackend:
    """test_prompt_dispatches_to_backend"""

    @pytest.mark.asyncio
    async def test_prompt_dispatches_to_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OMCAgent.prompt() should call backend.execute with the prompt."""
        _make_env(monkeypatch)

        from onemancompany.acp.agent_process import OMCAgent

        agent = OMCAgent()
        mock_backend = _make_mock_backend()
        agent._backend = mock_backend

        # Create a mock client that the agent calls for session_update
        mock_client = MagicMock()
        mock_client.session_update = AsyncMock()
        agent._client = mock_client

        # Create a minimal TextContentBlock
        from onemancompany.acp.adapter import TextContentBlock

        prompt_blocks = [TextContentBlock(type="text", text="Hello, employee!")]

        response = await agent.prompt(
            prompt=prompt_blocks,
            session_id="test-emp-001",
        )

        mock_backend.execute.assert_called_once()
        assert response is not None
        assert response.stop_reason in ("end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled")


class TestCancelStopsExecution:
    """test_cancel_stops_execution"""

    @pytest.mark.asyncio
    async def test_cancel_stops_execution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OMCAgent.cancel() should set the cancel event."""
        _make_env(monkeypatch)

        from onemancompany.acp.agent_process import OMCAgent

        agent = OMCAgent()
        mock_backend = _make_mock_backend()
        agent._backend = mock_backend

        # Cancel event should start unset
        assert not agent._cancel_event.is_set()

        await agent.cancel(session_id="test-emp-001")

        assert agent._cancel_event.is_set()
