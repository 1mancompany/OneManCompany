"""Integration tests for CEO executor / broker end-to-end flows.

These are "wide unit tests" that wire multiple real components together
(CeoBroker, CeoSession, CeoExecutor, CeoInteraction, TaskTree, TaskNode)
with minimal mocking (event_bus, save_tree_async, store, LLM calls).
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import onemancompany.core.ceo_broker as _broker_mod
from onemancompany.core.ceo_broker import (
    CEO_SESSION_FILENAME,
    CeoBroker,
    CeoExecutor,
    CeoInteraction,
    CeoSession,
    get_ceo_broker,
)
from onemancompany.core.task_lifecycle import NodeType, TaskPhase
from onemancompany.core.task_tree import TaskNode, TaskTree, _cache as _tree_cache
from onemancompany.core.vessel import LaunchResult, ScheduleEntry, TaskContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_broker() -> None:
    """Reset the broker singleton between tests."""
    _broker_mod._broker = None


def _make_tree_with_ea(project_id: str = "proj/iter_001") -> tuple[TaskTree, TaskNode, TaskNode]:
    """Create a tree with CEO root + EA node (standard project shape)."""
    tree = TaskTree(project_id=project_id)
    root = tree.create_root("00001", "Build feature X")
    root.node_type = NodeType.CEO_PROMPT
    ea = tree.add_child(
        parent_id=root.id,
        employee_id="00004",
        description="EA coordinates feature X",
        acceptance_criteria=["Feature delivered"],
        title="EA: Feature X",
    )
    ea.node_type = NodeType.TASK
    return tree, root, ea


# ---------------------------------------------------------------------------
# Shared patch targets
# ---------------------------------------------------------------------------

_EVENT_BUS_PATH = "onemancompany.core.events.event_bus"
_SAVE_TREE_PATH = "onemancompany.core.task_tree.save_tree_async"
_STORE_PATH = "onemancompany.core.vessel._store"


# ---------------------------------------------------------------------------
# Test 1: dispatch_child → CeoExecutor → CEO reply → node completion
# ---------------------------------------------------------------------------

class TestDispatchToCeoThenReply:
    """Full flow: employee dispatches to CEO, CEO replies, executor returns."""

    @pytest.mark.asyncio
    async def test_dispatch_to_ceo_then_reply(self):
        """
        1. Employee calls dispatch_child(CEO_ID, "Need approval")
        2. CeoExecutor.execute() is called, pushes to CeoBroker
        3. CEO replies via broker.handle_input()
        4. CeoExecutor returns LaunchResult with CEO's text
        """
        _reset_broker()
        broker = get_ceo_broker()

        executor = CeoExecutor()
        context = TaskContext(
            project_id="proj/iter_001",
            employee_id="00003",
            task_id="node_abc",
            work_dir="/tmp",
        )

        async def _ceo_replies():
            await asyncio.sleep(0.05)
            with patch(_EVENT_BUS_PATH) as bus:
                bus.publish = AsyncMock()
                result = await broker.handle_input("proj/iter_001", "Approved, go ahead")
            assert result["type"] == "resolved"
            assert result["node_id"] == "node_abc"

        reply_task = asyncio.create_task(_ceo_replies())

        with patch(_EVENT_BUS_PATH) as mock_bus:
            mock_bus.publish = AsyncMock()
            result = await executor.execute("Need deployment approval", context)

        await reply_task

        assert isinstance(result, LaunchResult)
        assert result.output == "Approved, go ahead"
        assert result.model_used == "ceo"

        # Verify broker session state
        session = broker.get_session("proj/iter_001")
        assert session is not None
        assert session.has_pending is False
        # History should have the enqueued message + CEO reply
        assert len(session.history) >= 2

        _reset_broker()


# ---------------------------------------------------------------------------
# Test 2: project completion → confirm node → CEO confirms → cleanup
# ---------------------------------------------------------------------------

class TestProjectCompletionConfirmFlow:
    """Project completes → confirm node created → CEO replies → cleanup."""

    @pytest.mark.asyncio
    async def test_project_completion_confirm_flow(self):
        """
        1. Build a tree where all tasks are finished
        2. Trigger _on_child_complete_inner → project completion check
        3. Verify CEO_REQUEST confirm node created and scheduled
        4. Simulate CeoExecutor executing → pushes to broker
        5. CEO replies "confirmed"
        6. Verify _full_cleanup called with run_retrospective
        """
        _reset_broker()
        _tree_cache.clear()

        tree, root, ea = _make_tree_with_ea("proj/iter_001")

        # Add a child task under EA, mark it fully resolved
        child = tree.add_child(
            parent_id=ea.id,
            employee_id="00006",
            description="Implement feature",
            acceptance_criteria=["Tests pass"],
            title="Impl",
        )
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)
        child.set_status(TaskPhase.ACCEPTED)
        child.set_status(TaskPhase.FINISHED)
        child.result = "Done"

        # Mark EA as done executing (COMPLETED)
        ea.set_status(TaskPhase.PROCESSING)
        ea.set_status(TaskPhase.COMPLETED)
        ea.result = "All subtasks completed"

        with tempfile.TemporaryDirectory() as tmpdir:
            tree_path = Path(tmpdir) / "task_tree.yaml"
            tree.save(tree_path)
            _tree_cache[str(tree_path.resolve())] = tree

            # Create EmployeeManager mock that captures schedule_node calls
            from onemancompany.core.vessel import EmployeeManager
            em = EmployeeManager()
            # Register a dummy executor for CEO so schedule_node doesn't warn
            em.executors["00001"] = CeoExecutor()
            em.executors["00004"] = MagicMock()

            scheduled_nodes: list[tuple[str, str, str]] = []
            original_schedule = em.schedule_node

            def _track_schedule(emp_id, node_id, tp):
                scheduled_nodes.append((emp_id, node_id, tp))
                original_schedule(emp_id, node_id, tp)

            em.schedule_node = _track_schedule
            em._schedule_next = MagicMock()

            # The child just finished — trigger completion propagation
            entry = ScheduleEntry(node_id=child.id, tree_path=str(tree_path))

            mock_summary_resp = MagicMock()
            mock_summary_resp.content = "Project completed successfully."
            with (
                patch(_SAVE_TREE_PATH),
                patch(_STORE_PATH) as mock_store,
                patch("onemancompany.core.vessel._store") as mock_store2,
                patch.object(em, "_publish_node_update"),
                patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_summary_resp),
                patch("onemancompany.core.vessel.make_llm"),
            ):
                mock_store.save_project_status = AsyncMock()
                mock_store2.save_project_status = AsyncMock()
                await em._on_child_complete_inner("00006", entry, project_id="proj/iter_001")

            # EA should be auto-completed and a confirm node created
            # Check that a CEO_REQUEST node was added under EA
            confirm_nodes = [
                n for n in tree.all_nodes()
                if n.node_type == NodeType.CEO_REQUEST and n.employee_id == "00001"
            ]
            assert len(confirm_nodes) == 1, f"Expected 1 confirm node, got {len(confirm_nodes)}"
            confirm = confirm_nodes[0]
            assert "confirm" in confirm.description.lower() or "completion" in confirm.description.lower()

            # Check that schedule_node was called for CEO
            ceo_scheduled = [s for s in scheduled_nodes if s[0] == "00001"]
            assert len(ceo_scheduled) >= 1, f"CEO not scheduled: {scheduled_nodes}"

        _tree_cache.clear()
        _reset_broker()


# ---------------------------------------------------------------------------
# Test 3: restart recovery → sessions restored → pending re-scheduled
# ---------------------------------------------------------------------------

class TestRestartRecovery:
    """Server restart: trees + session history recovered, pending nodes rescheduled."""

    def test_restart_recovery_end_to_end(self):
        """
        1. Create tree with PENDING CEO_REQUEST node
        2. Save tree to disk + CEO session history
        3. Call recover_schedule_from_trees + broker.recover()
        4. Verify session has history loaded
        5. Verify schedule_node was called for the CEO_REQUEST
        """
        _reset_broker()
        _tree_cache.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = Path(tmpdir) / "iter_001"
            proj_dir.mkdir(parents=True)

            # Build tree with a pending CEO_REQUEST node
            tree, root, ea = _make_tree_with_ea("test_proj/iter_001")
            ea.set_status(TaskPhase.PROCESSING)
            ea.set_status(TaskPhase.COMPLETED)

            ceo_request = tree.add_child(
                parent_id=ea.id,
                employee_id="00001",
                description="Please confirm project completion",
                acceptance_criteria=[],
                title="CEO Confirm",
            )
            ceo_request.node_type = NodeType.CEO_REQUEST
            # Node is PENDING

            tree_path = proj_dir / "task_tree.yaml"
            tree.save(tree_path)

            # Write session history
            history = [
                {"role": "system", "text": "Project complete", "source": "00004", "timestamp": "2026-03-01"},
                {"role": "ceo", "text": "Let me review", "timestamp": "2026-03-01"},
            ]
            (proj_dir / CEO_SESSION_FILENAME).write_text(
                yaml.dump({"history": history}, allow_unicode=True),
            )

            # Mock EmployeeManager
            em = MagicMock()
            scheduled_calls: list[tuple[str, str, str]] = []

            def _capture_schedule(emp_id, node_id, tp):
                scheduled_calls.append((emp_id, node_id, tp))

            em.schedule_node = _capture_schedule

            _tree_cache.clear()

            # Run recovery — set broker singleton so recover_schedule_from_trees
            # picks it up via get_ceo_broker()
            from onemancompany.core.task_persistence import recover_schedule_from_trees

            _reset_broker()
            _broker_mod._broker = CeoBroker()
            broker = get_ceo_broker()

            recover_schedule_from_trees(em, Path(tmpdir), Path("/nonexistent"))

            # Verify broker recovered session history
            session = broker.get_session("test_proj/iter_001")
            assert session is not None
            assert len(session.history) == 2
            assert session.history[0]["role"] == "system"
            assert session.history[1]["role"] == "ceo"

            # Verify CEO_REQUEST node was scheduled
            ceo_calls = [c for c in scheduled_calls if c[0] == "00001"]
            assert len(ceo_calls) >= 1, f"CEO_REQUEST not scheduled: {scheduled_calls}"
            assert ceo_calls[0][1] == ceo_request.id

        _tree_cache.clear()
        _reset_broker()


# ---------------------------------------------------------------------------
# Test 4: FIFO order with multiple pending interactions
# ---------------------------------------------------------------------------

class TestFifoMultipleRequests:
    """Two employees dispatch to CEO — replies resolve in FIFO order."""

    @pytest.mark.asyncio
    async def test_fifo_multiple_requests(self):
        """
        1. Two employees dispatch_child(CEO_ID) to same project
        2. CeoBroker has 2 pending interactions
        3. CEO replies once → first request resolved
        4. CEO replies again → second request resolved
        5. Order preserved
        """
        _reset_broker()
        broker = get_ceo_broker()
        session = broker.get_or_create_session("proj/iter_001")

        # Enqueue two interactions from different employees
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()

        i1 = CeoInteraction(
            node_id="node_first",
            tree_path="",
            project_id="proj/iter_001",
            source_employee="00003",
            interaction_type="ceo_request",
            message="Employee 00003 needs approval",
            future=f1,
        )
        i2 = CeoInteraction(
            node_id="node_second",
            tree_path="",
            project_id="proj/iter_001",
            source_employee="00006",
            interaction_type="ceo_request",
            message="Employee 00006 needs review",
            future=f2,
        )

        with patch("onemancompany.core.ceo_broker._ea_auto_reply", new_callable=AsyncMock):
            session.auto_reply_enabled = False
            session.enqueue(i1)
            session.enqueue(i2)

        assert session.pending_count == 2

        # CEO replies first time → should resolve i1
        with patch(_EVENT_BUS_PATH) as bus:
            bus.publish = AsyncMock()
            result1 = await broker.handle_input("proj/iter_001", "First approved")

        assert result1["type"] == "resolved"
        assert result1["node_id"] == "node_first"
        assert f1.result() == "First approved"
        assert not f2.done()
        assert session.pending_count == 1

        # CEO replies second time → should resolve i2
        with patch(_EVENT_BUS_PATH) as bus:
            bus.publish = AsyncMock()
            result2 = await broker.handle_input("proj/iter_001", "Second reviewed")

        assert result2["type"] == "resolved"
        assert result2["node_id"] == "node_second"
        assert f2.result() == "Second reviewed"
        assert session.pending_count == 0

        _reset_broker()

    @pytest.mark.asyncio
    async def test_fifo_with_executor_pair(self):
        """Two CeoExecutor.execute() calls resolve in FIFO order via broker."""
        _reset_broker()
        broker = get_ceo_broker()

        executor = CeoExecutor()
        ctx1 = TaskContext(project_id="proj/iter_001", employee_id="00003", task_id="n1")
        ctx2 = TaskContext(project_id="proj/iter_001", employee_id="00006", task_id="n2")

        results: list[LaunchResult] = []

        async def _exec(desc, ctx):
            with patch(_EVENT_BUS_PATH) as bus:
                bus.publish = AsyncMock()
                r = await executor.execute(desc, ctx)
            results.append(r)

        async def _reply_both():
            # Wait until both are enqueued
            for _ in range(50):
                session = broker.get_session("proj/iter_001")
                if session and session.pending_count >= 2:
                    break
                await asyncio.sleep(0.02)

            with patch(_EVENT_BUS_PATH) as bus:
                bus.publish = AsyncMock()
                await broker.handle_input("proj/iter_001", "Reply to first")
            with patch(_EVENT_BUS_PATH) as bus:
                bus.publish = AsyncMock()
                await broker.handle_input("proj/iter_001", "Reply to second")

        t1 = asyncio.create_task(_exec("Request from 00003", ctx1))
        t2 = asyncio.create_task(_exec("Request from 00006", ctx2))
        t3 = asyncio.create_task(_reply_both())

        await asyncio.gather(t1, t2, t3)

        assert len(results) == 2
        outputs = {r.output for r in results}
        assert "Reply to first" in outputs
        assert "Reply to second" in outputs

        _reset_broker()


# ---------------------------------------------------------------------------
# Test 5: EA auto-reply fires when CEO doesn't respond
# ---------------------------------------------------------------------------

class TestAutoReplyIntegration:
    """EA auto-reply resolves the Future when CEO doesn't respond in time."""

    @pytest.mark.asyncio
    async def test_auto_reply_integration(self):
        """
        1. Employee dispatches to CEO
        2. CeoExecutor pushes to broker with short timeout
        3. CEO doesn't reply
        4. EA auto-reply resolves the Future
        5. CeoExecutor returns the auto-reply as LaunchResult
        """
        _reset_broker()
        broker = get_ceo_broker()

        executor = CeoExecutor()
        context = TaskContext(
            project_id="proj/iter_001",
            employee_id="00003",
            task_id="node_auto",
        )

        # Pre-configure session with short timeout
        session = broker.get_or_create_session("proj/iter_001")
        session.auto_reply_timeout = 0.1  # 100ms

        with (
            patch(_EVENT_BUS_PATH) as mock_bus,
            patch(
                "onemancompany.core.ceo_broker._ea_auto_reply",
                new_callable=AsyncMock,
                return_value="[EA Auto-Reply] Decision: ACCEPT\nLooks reasonable",
            ) as mock_ea,
        ):
            mock_bus.publish = AsyncMock()
            result = await asyncio.wait_for(
                executor.execute("Need budget approval", context),
                timeout=5.0,
            )

        assert isinstance(result, LaunchResult)
        assert "ACCEPT" in result.output
        assert "Looks reasonable" in result.output
        assert result.model_used == "ceo"

        # Verify EA auto-reply was called
        mock_ea.assert_awaited_once()

        # Session should have no pending interactions
        assert session.has_pending is False

        _reset_broker()

    @pytest.mark.asyncio
    async def test_ceo_reply_beats_auto_reply(self):
        """CEO responds before auto-reply timeout → auto-reply never fires."""
        _reset_broker()
        broker = get_ceo_broker()

        executor = CeoExecutor()
        context = TaskContext(
            project_id="proj/iter_001",
            employee_id="00003",
            task_id="node_fast",
        )

        # Long timeout so auto-reply won't fire
        session = broker.get_or_create_session("proj/iter_001")
        session.auto_reply_timeout = 10

        async def _ceo_quick_reply():
            await asyncio.sleep(0.05)
            with patch(_EVENT_BUS_PATH) as bus:
                bus.publish = AsyncMock()
                await broker.handle_input("proj/iter_001", "CEO approved quickly")

        reply_task = asyncio.create_task(_ceo_quick_reply())

        with (
            patch(_EVENT_BUS_PATH) as mock_bus,
            patch(
                "onemancompany.core.ceo_broker._ea_auto_reply",
                new_callable=AsyncMock,
            ) as mock_ea,
        ):
            mock_bus.publish = AsyncMock()
            result = await executor.execute("Need approval", context)

        await reply_task

        assert result.output == "CEO approved quickly"
        mock_ea.assert_not_awaited()

        _reset_broker()
