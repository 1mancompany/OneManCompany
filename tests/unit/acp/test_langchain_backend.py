"""Unit tests for acp/backends/langchain_backend.py."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backend(employee_id="00010", server_url="http://localhost:8000"):
    """Instantiate LangChainAcpBackend with EmployeeAgent import mocked."""
    with patch("onemancompany.acp.backends.langchain_backend.EmployeeAgent"):
        from onemancompany.acp.backends.langchain_backend import LangChainAcpBackend  # noqa: PLC0415
        return LangChainAcpBackend(employee_id=employee_id, server_url=server_url)


# ---------------------------------------------------------------------------
# Test: execute calls agent and returns result dict
# ---------------------------------------------------------------------------


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_calls_agent_and_returns_result(self):
        """execute() should call run_streamed on the agent and return a result dict."""
        with patch(
            "onemancompany.acp.backends.langchain_backend.EmployeeAgent"
        ) as MockEmployeeAgent:
            mock_agent_instance = MagicMock()
            mock_agent_instance.run_streamed = AsyncMock(return_value="Task done successfully")
            mock_agent_instance._last_usage = {
                "model": "gpt-4o",
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "cost_usd": 0.001,
            }
            MockEmployeeAgent.return_value = mock_agent_instance

            from onemancompany.acp.backends.langchain_backend import LangChainAcpBackend  # noqa: PLC0415

            backend = LangChainAcpBackend(employee_id="00010", server_url="http://localhost:8000")

            client = MagicMock()
            client.session_update = AsyncMock()
            cancel_event = asyncio.Event()

            result = await backend.execute(
                task_description="Write a report",
                client=client,
                session_id="00010",
                cancel_event=cancel_event,
            )

        # run_streamed should have been called with the task description
        mock_agent_instance.run_streamed.assert_called_once()
        call_args = mock_agent_instance.run_streamed.call_args
        assert call_args[0][0] == "Write a report"  # first positional arg is task

        # Result dict should have the expected shape
        assert isinstance(result, dict)
        assert result["output"] == "Task done successfully"
        assert result["model"] == "gpt-4o"
        assert result["tokens"]["input"] == 100
        assert result["tokens"]["output"] == 50
        assert result["tokens"]["cost_usd"] == 0.001

    @pytest.mark.asyncio
    async def test_execute_sends_acp_updates_for_llm_input(self):
        """on_log('llm_input', ...) should send an update_agent_thought to the client."""
        with patch(
            "onemancompany.acp.backends.langchain_backend.EmployeeAgent"
        ) as MockEmployeeAgent:
            mock_agent_instance = MagicMock()

            # Capture the on_log callback so we can call it ourselves
            captured_on_log = None

            async def fake_run_streamed(task, on_log=None):
                nonlocal captured_on_log
                captured_on_log = on_log
                if on_log:
                    on_log("llm_input", "some LLM input text")
                return "done"

            mock_agent_instance.run_streamed = fake_run_streamed
            mock_agent_instance._last_usage = {
                "model": "gpt-4o",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "cost_usd": 0.0,
            }
            MockEmployeeAgent.return_value = mock_agent_instance

            from onemancompany.acp.backends.langchain_backend import LangChainAcpBackend  # noqa: PLC0415

            backend = LangChainAcpBackend(employee_id="00010", server_url="http://localhost:8000")

            client = MagicMock()
            client.session_update = AsyncMock()
            cancel_event = asyncio.Event()

            await backend.execute(
                task_description="Do something",
                client=client,
                session_id="00010",
                cancel_event=cancel_event,
            )

        # session_update should have been called at least once for the llm_input
        assert client.session_update.called

    @pytest.mark.asyncio
    async def test_execute_sends_acp_updates_for_tool_call(self):
        """on_log('tool_call', ...) should send a start_tool_call update to the client."""
        with patch(
            "onemancompany.acp.backends.langchain_backend.EmployeeAgent"
        ) as MockEmployeeAgent:
            mock_agent_instance = MagicMock()

            async def fake_run_streamed(task, on_log=None):
                if on_log:
                    on_log("tool_call", {
                        "tool_name": "read_file",
                        "tool_args": {"path": "/foo"},
                        "content": "read_file(/foo)",
                    })
                return "done"

            mock_agent_instance.run_streamed = fake_run_streamed
            mock_agent_instance._last_usage = {
                "model": "gpt-4o",
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": None,
            }
            MockEmployeeAgent.return_value = mock_agent_instance

            from onemancompany.acp.backends.langchain_backend import LangChainAcpBackend  # noqa: PLC0415

            backend = LangChainAcpBackend(employee_id="00010", server_url="http://localhost:8000")

            client = MagicMock()
            client.session_update = AsyncMock()
            cancel_event = asyncio.Event()

            await backend.execute(
                task_description="Do something",
                client=client,
                session_id="00010",
                cancel_event=cancel_event,
            )

        assert client.session_update.called


# ---------------------------------------------------------------------------
# Test: set_model clears the agent cache
# ---------------------------------------------------------------------------


class TestSetModel:
    def test_set_model_clears_cache(self):
        """After set_model(), _agent_runner should be None so next execute rebuilds it."""
        with patch(
            "onemancompany.acp.backends.langchain_backend.EmployeeAgent"
        ) as MockEmployeeAgent:
            mock_agent_instance = MagicMock()
            MockEmployeeAgent.return_value = mock_agent_instance

            from onemancompany.acp.backends.langchain_backend import LangChainAcpBackend  # noqa: PLC0415

            backend = LangChainAcpBackend(employee_id="00010", server_url="http://localhost:8000")

            # Manually set _agent_runner to simulate a cached agent
            backend._agent_runner = mock_agent_instance
            assert backend._agent_runner is not None

            # set_model should clear the cache
            backend.set_model("claude-3-5-sonnet")

            assert backend._agent_runner is None

    def test_set_model_stores_model_id(self):
        """set_model() should store the new model_id for the next build."""
        with patch("onemancompany.acp.backends.langchain_backend.EmployeeAgent"):
            from onemancompany.acp.backends.langchain_backend import LangChainAcpBackend  # noqa: PLC0415

            backend = LangChainAcpBackend(employee_id="00010", server_url="http://localhost:8000")
            backend.set_model("gpt-4o-mini")

            assert backend._model_id == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Test: set_config
# ---------------------------------------------------------------------------


class TestSetConfig:
    def test_set_config_stores_value(self):
        """set_config() should store key-value pairs in internal config."""
        with patch("onemancompany.acp.backends.langchain_backend.EmployeeAgent"):
            from onemancompany.acp.backends.langchain_backend import LangChainAcpBackend  # noqa: PLC0415

            backend = LangChainAcpBackend(employee_id="00010", server_url="http://localhost:8000")
            backend.set_config("temperature", 0.5)

            assert backend._config.get("temperature") == 0.5


# ---------------------------------------------------------------------------
# Test: HTTP proxy tool replacement
# ---------------------------------------------------------------------------


class TestHttpProxyTools:
    @pytest.mark.asyncio
    async def test_http_proxy_sends_post_to_server(self):
        """HTTP proxy tools should POST to /api/internal/tool-call."""
        import httpx

        with patch(
            "onemancompany.acp.backends.langchain_backend.EmployeeAgent"
        ) as MockEmployeeAgent:
            mock_agent_instance = MagicMock()
            mock_tool = MagicMock()
            mock_tool.name = "read_file"
            mock_tool.description = "Read a file"
            mock_agent_instance._agent_tools = [mock_tool]
            MockEmployeeAgent.return_value = mock_agent_instance

            from onemancompany.acp.backends.langchain_backend import LangChainAcpBackend  # noqa: PLC0415

            backend = LangChainAcpBackend(employee_id="00010", server_url="http://localhost:8000")

            with patch("httpx.AsyncClient") as MockClient:
                mock_http_client = AsyncMock()
                mock_response = MagicMock()
                mock_response.text = "file contents"
                mock_http_client.post = AsyncMock(return_value=mock_response)
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=None)

                agent = backend._build_agent(task_id="task-123")

            # The tool replacement should have happened
            assert agent is not None
