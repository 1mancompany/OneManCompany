"""Tests for CeoBroker data structures and persistence."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from onemancompany.core.ceo_broker import CeoInteraction, CeoSession


class TestCeoInteraction:
    def test_creation(self):
        future = asyncio.get_event_loop().create_future()
        interaction = CeoInteraction(
            node_id="abc123",
            tree_path="/tmp/tree.yaml",
            project_id="proj_001/iter_001",
            source_employee="00003",
            interaction_type="ceo_request",
            message="Alex requests deployment approval",
            future=future,
        )
        assert interaction.node_id == "abc123"
        assert interaction.interaction_type == "ceo_request"
        assert interaction.created_at  # auto-filled


class TestCeoSession:
    def test_push_system_message(self):
        session = CeoSession(project_id="proj_001")
        session.push_system_message("Deploy approval needed", source="00003")
        assert len(session.history) == 1
        assert session.history[0]["role"] == "system"
        assert session.history[0]["source"] == "00003"

    def test_push_ceo_message(self):
        session = CeoSession(project_id="proj_001")
        session.push_ceo_message("Approved")
        assert len(session.history) == 1
        assert session.history[0]["role"] == "ceo"

    def test_enqueue_and_has_pending(self):
        session = CeoSession(project_id="proj_001")
        assert session.has_pending is False
        future = asyncio.get_event_loop().create_future()
        interaction = CeoInteraction(
            node_id="abc",
            tree_path="/tmp/t.yaml",
            project_id="proj_001",
            source_employee="00003",
            interaction_type="ceo_request",
            message="Need approval",
            future=future,
        )
        session.enqueue(interaction)
        assert session.has_pending is True
        assert session.pending_count == 1

    def test_save_and_load_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session = CeoSession(project_id="proj_001")
            session.push_system_message("Hello", source="00003")
            session.push_ceo_message("Hi")
            session.save_history(Path(tmpdir))

            session2 = CeoSession(project_id="proj_001")
            session2.load_history(Path(tmpdir))
            assert len(session2.history) == 2
            assert session2.history[0]["role"] == "system"
            assert session2.history[1]["role"] == "ceo"

    def test_fifo_order(self):
        session = CeoSession(project_id="proj_001")
        loop = asyncio.get_event_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()
        i1 = CeoInteraction(
            node_id="first", tree_path="", project_id="proj_001",
            source_employee="00003", interaction_type="ceo_request",
            message="First", future=f1,
        )
        i2 = CeoInteraction(
            node_id="second", tree_path="", project_id="proj_001",
            source_employee="00004", interaction_type="project_confirm",
            message="Second", future=f2,
        )
        session.enqueue(i1)
        session.enqueue(i2)
        popped = session.pop_pending()
        assert popped.node_id == "first"
        assert session.pending_count == 1


class TestCeoBroker:
    def test_get_or_create_session(self):
        from onemancompany.core.ceo_broker import CeoBroker
        broker = CeoBroker()
        session = broker.get_or_create_session("proj_001")
        assert session.project_id == "proj_001"
        session2 = broker.get_or_create_session("proj_001")
        assert session is session2

    def test_list_sessions_sorted_by_pending(self):
        from onemancompany.core.ceo_broker import CeoBroker, CeoInteraction
        broker = CeoBroker()
        s1 = broker.get_or_create_session("proj_no_pending")
        s2 = broker.get_or_create_session("proj_with_pending")
        loop = asyncio.get_event_loop()
        s2.enqueue(CeoInteraction(
            node_id="x", tree_path="", project_id="proj_with_pending",
            source_employee="00003", interaction_type="ceo_request",
            message="Help", future=loop.create_future(),
        ))
        summaries = broker.list_sessions()
        assert summaries[0]["project_id"] == "proj_with_pending"
        assert summaries[1]["project_id"] == "proj_no_pending"

    @pytest.mark.asyncio
    async def test_handle_input_resolves_pending(self):
        from onemancompany.core.ceo_broker import CeoBroker, CeoInteraction
        broker = CeoBroker()
        session = broker.get_or_create_session("proj_001")
        future = asyncio.get_event_loop().create_future()
        session.enqueue(CeoInteraction(
            node_id="abc", tree_path="", project_id="proj_001",
            source_employee="00003", interaction_type="ceo_request",
            message="Need approval", future=future,
        ))
        result = await broker.handle_input("proj_001", "Approved")
        assert result["type"] == "resolved"
        assert result["node_id"] == "abc"
        assert future.result() == "Approved"
        assert session.has_pending is False

    @pytest.mark.asyncio
    async def test_handle_input_no_pending_returns_followup(self):
        from onemancompany.core.ceo_broker import CeoBroker
        broker = CeoBroker()
        broker.get_or_create_session("proj_001")
        result = await broker.handle_input("proj_001", "Do more work")
        assert result["type"] == "followup"
        assert result["text"] == "Do more work"


class TestCeoExecutor:
    @pytest.mark.asyncio
    async def test_execute_enqueues_and_waits(self):
        """CeoExecutor.execute() should enqueue interaction and await CEO reply."""
        from onemancompany.core.ceo_broker import CeoExecutor, get_ceo_broker
        from onemancompany.core.vessel import TaskContext, LaunchResult
        import onemancompany.core.ceo_broker as _mod

        _mod._broker = None
        broker = get_ceo_broker()

        executor = CeoExecutor()
        context = TaskContext(
            project_id="proj_001/iter_001",
            work_dir="/tmp",
            employee_id="00001",
            task_id="node_abc",
        )

        # Simulate CEO replying after a short delay
        async def _reply_later():
            await asyncio.sleep(0.05)
            session = broker.get_session("proj_001/iter_001")
            interaction = session.pop_pending()
            interaction.future.set_result("CEO says approved")

        reply_task = asyncio.create_task(_reply_later())

        with patch("onemancompany.core.events.event_bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            result = await executor.execute("Deploy approval needed", context)

        await reply_task
        assert isinstance(result, LaunchResult)
        assert result.output == "CEO says approved"
        assert result.model_used == "ceo"

        _mod._broker = None

    def test_is_ready(self):
        from onemancompany.core.ceo_broker import CeoExecutor
        assert CeoExecutor().is_ready() is True


class TestCeoBrokerRecovery:
    def test_recover_loads_session_history(self):
        """recover() should load ceo_session.yaml into sessions."""
        import tempfile
        from onemancompany.core.ceo_broker import CeoBroker, CEO_SESSION_FILENAME
        from onemancompany.core.task_tree import TaskTree, _cache

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a project dir with a tree and session history
            proj_dir = Path(tmpdir) / "iter_001"
            proj_dir.mkdir(parents=True)

            tree = TaskTree(project_id="test_proj/iter_001")
            tree.create_root("00001", "Test task")
            tree_path = proj_dir / "task_tree.yaml"
            tree.save(tree_path)

            # Write session history
            history = [
                {"role": "system", "text": "Approve?", "source": "00003", "timestamp": "2026-01-01"},
                {"role": "ceo", "text": "Approved", "timestamp": "2026-01-01"},
            ]
            (proj_dir / CEO_SESSION_FILENAME).write_text(
                yaml.dump({"history": history}, allow_unicode=True),
            )

            _cache.clear()
            broker = CeoBroker()
            broker.recover(Path(tmpdir))

            session = broker.get_session("test_proj/iter_001")
            assert session is not None
            assert len(session.history) == 2
            assert session.history[0]["role"] == "system"
            assert session.history[1]["role"] == "ceo"

            _cache.clear()

    def test_recover_skips_missing_history(self):
        """recover() should create session but skip history loading if no file."""
        import tempfile
        from onemancompany.core.ceo_broker import CeoBroker
        from onemancompany.core.task_tree import TaskTree, _cache

        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = Path(tmpdir) / "iter_001"
            proj_dir.mkdir(parents=True)

            tree = TaskTree(project_id="test_proj/iter_001")
            tree.create_root("00001", "Test task")
            tree.save(proj_dir / "task_tree.yaml")

            _cache.clear()
            broker = CeoBroker()
            broker.recover(Path(tmpdir))

            # Session should exist but with no history
            session = broker.get_session("test_proj/iter_001")
            assert session is not None
            assert len(session.history) == 0

            _cache.clear()


class TestCeoRegistration:
    def test_ceo_executor_registered_in_executors(self):
        """CeoExecutor should be registerable in EmployeeManager.executors."""
        from onemancompany.core.ceo_broker import CeoExecutor
        from onemancompany.core.config import CEO_ID

        executor = CeoExecutor()
        executors = {}
        executors[CEO_ID] = executor

        assert CEO_ID in executors
        assert isinstance(executors[CEO_ID], CeoExecutor)
        assert executor.is_ready()
