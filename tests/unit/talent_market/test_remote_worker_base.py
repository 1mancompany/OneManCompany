"""Unit tests for talent_market/remote_worker_base.py — RemoteWorkerBase class."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from onemancompany.talent_market.remote_protocol import TaskAssignment, TaskResult
from onemancompany.talent_market.remote_worker_base import RemoteWorkerBase


# ---------------------------------------------------------------------------
# Concrete test subclass
# ---------------------------------------------------------------------------


class DummyWorker(RemoteWorkerBase):
    """Concrete subclass for testing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tools_setup = False
        self.processed_tasks: list[TaskAssignment] = []

    def setup_tools(self) -> list:
        self.tools_setup = True
        return ["tool_a", "tool_b"]

    async def process_task(self, task: TaskAssignment) -> TaskResult:
        self.processed_tasks.append(task)
        return TaskResult(
            task_id=task.task_id,
            employee_id=self.employee_id,
            status="completed",
            output=f"Processed {task.task_id}",
        )


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestRemoteWorkerBaseInit:
    def test_defaults(self):
        w = DummyWorker("http://localhost:8000", "00010")
        assert w.company_url == "http://localhost:8000"
        assert w.employee_id == "00010"
        assert w.capabilities == []
        assert w.heartbeat_interval == 30.0
        assert w.poll_interval == 5.0
        assert w._running is False
        assert w._current_task_id is None

    def test_trailing_slash_stripped(self):
        w = DummyWorker("http://localhost:8000/", "00010")
        assert w.company_url == "http://localhost:8000"

    def test_custom_params(self):
        w = DummyWorker(
            "http://server:9000",
            "00020",
            capabilities=["coding", "testing"],
            heartbeat_interval=60.0,
            poll_interval=10.0,
        )
        assert w.capabilities == ["coding", "testing"]
        assert w.heartbeat_interval == 60.0
        assert w.poll_interval == 10.0


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


class TestStop:
    def test_stop_sets_running_false(self):
        w = DummyWorker("http://localhost:8000", "00010")
        w._running = True
        w.stop()
        assert w._running is False


# ---------------------------------------------------------------------------
# _register
# ---------------------------------------------------------------------------


class TestRegister:
    async def test_register_posts_to_correct_url(self):
        w = DummyWorker("http://localhost:8000", "00010", capabilities=["coding"])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("onemancompany.talent_market.remote_worker_base.httpx.AsyncClient", return_value=mock_client):
            await w._register()

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://localhost:8000/api/remote/register"
        payload = call_args[1]["json"]
        assert payload["employee_id"] == "00010"
        assert payload["capabilities"] == ["coding"]

    async def test_register_raises_on_error(self):
        w = DummyWorker("http://localhost:8000", "00010")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=mock_response))

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("onemancompany.talent_market.remote_worker_base.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                await w._register()


# ---------------------------------------------------------------------------
# _poll_loop
# ---------------------------------------------------------------------------


class TestPollLoop:
    async def test_processes_task_and_submits_result(self):
        w = DummyWorker("http://localhost:8000", "00010", poll_interval=0.01)
        w._running = True

        task_data = {
            "task": {
                "task_id": "t1",
                "project_id": "p1",
                "task_description": "Do X",
            }
        }
        # First call returns a task, second call returns no task, then stop
        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.status_code = 200
                resp.json.return_value = task_data
            else:
                resp.status_code = 200
                resp.json.return_value = {"task": None}
                w._running = False  # Stop after second poll
            return resp

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("onemancompany.talent_market.remote_worker_base.httpx.AsyncClient", return_value=mock_client):
            await w._poll_loop()

        assert len(w.processed_tasks) == 1
        assert w.processed_tasks[0].task_id == "t1"

    async def test_handles_poll_error_gracefully(self):
        w = DummyWorker("http://localhost:8000", "00010", poll_interval=0.01)
        w._running = True

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Connection refused")
            w._running = False
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"task": None}
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("onemancompany.talent_market.remote_worker_base.httpx.AsyncClient", return_value=mock_client):
            await w._poll_loop()  # Should not raise

        # Worker survived the error
        assert len(w.processed_tasks) == 0

    async def test_sets_current_task_id_during_processing(self):
        w = DummyWorker("http://localhost:8000", "00010", poll_interval=0.01)
        w._running = True

        captured_task_ids = []

        original_process = w.process_task

        async def capturing_process(task):
            captured_task_ids.append(w._current_task_id)
            return await original_process(task)

        w.process_task = capturing_process

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.status_code = 200
                resp.json.return_value = {
                    "task": {"task_id": "t99", "project_id": "p1", "task_description": "test"}
                }
            else:
                resp.status_code = 200
                resp.json.return_value = {"task": None}
                w._running = False
            return resp

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("onemancompany.talent_market.remote_worker_base.httpx.AsyncClient", return_value=mock_client):
            await w._poll_loop()

        assert captured_task_ids == ["t99"]
        assert w._current_task_id is None  # Cleared after processing


# ---------------------------------------------------------------------------
# _heartbeat_loop
# ---------------------------------------------------------------------------


class TestHeartbeatLoop:
    async def test_sends_heartbeat(self):
        w = DummyWorker("http://localhost:8000", "00010", heartbeat_interval=0.01)
        w._running = True

        post_calls = []

        async def mock_post(url, **kwargs):
            post_calls.append((url, kwargs))
            w._running = False  # Stop after first heartbeat
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("onemancompany.talent_market.remote_worker_base.httpx.AsyncClient", return_value=mock_client):
            await w._heartbeat_loop()

        assert len(post_calls) >= 1
        url, kwargs = post_calls[0]
        assert url == "http://localhost:8000/api/remote/heartbeat"
        payload = kwargs["json"]
        assert payload["employee_id"] == "00010"
        assert payload["status"] == "idle"

    async def test_heartbeat_busy_status(self):
        w = DummyWorker("http://localhost:8000", "00010", heartbeat_interval=0.01)
        w._running = True
        w._current_task_id = "task_abc"

        post_calls = []

        async def mock_post(url, **kwargs):
            post_calls.append(kwargs)
            w._running = False
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("onemancompany.talent_market.remote_worker_base.httpx.AsyncClient", return_value=mock_client):
            await w._heartbeat_loop()

        payload = post_calls[0]["json"]
        assert payload["status"] == "busy"
        assert payload["current_task_id"] == "task_abc"

    async def test_heartbeat_error_does_not_crash(self):
        w = DummyWorker("http://localhost:8000", "00010", heartbeat_interval=0.01)
        w._running = True

        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Network error")
            w._running = False
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("onemancompany.talent_market.remote_worker_base.httpx.AsyncClient", return_value=mock_client):
            await w._heartbeat_loop()  # Should not raise


# ---------------------------------------------------------------------------
# start (integration of register + loops)
# ---------------------------------------------------------------------------


class TestStart:
    async def test_start_calls_setup_tools_and_register(self):
        w = DummyWorker("http://localhost:8000", "00010", poll_interval=0.01, heartbeat_interval=0.01)

        # Mock _register to succeed
        w._register = AsyncMock()

        running_was_set = False

        # Mock the loops to stop immediately but capture that _running was True
        async def stop_immediately():
            nonlocal running_was_set
            if w._running:
                running_was_set = True
            w._running = False

        w._poll_loop = stop_immediately
        w._heartbeat_loop = stop_immediately

        await w.start()

        assert w.tools_setup is True
        w._register.assert_called_once()
        assert running_was_set is True  # _running was set to True before loops ran


# ---------------------------------------------------------------------------
# Abstract interface enforcement
# ---------------------------------------------------------------------------


class TestAbstractEnforcement:
    def test_cannot_instantiate_base_directly(self):
        with pytest.raises(TypeError):
            RemoteWorkerBase("http://localhost:8000", "00010")

    def test_must_implement_setup_tools(self):
        class MissingSetup(RemoteWorkerBase):
            async def process_task(self, task):
                pass

        with pytest.raises(TypeError):
            MissingSetup("http://localhost:8000", "00010")

    def test_must_implement_process_task(self):
        class MissingProcess(RemoteWorkerBase):
            def setup_tools(self):
                return []

        with pytest.raises(TypeError):
            MissingProcess("http://localhost:8000", "00010")
