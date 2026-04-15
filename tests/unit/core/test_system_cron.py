"""Tests for the system cron registry and manager."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from onemancompany.core.system_cron import (
    SystemCronDef,
    SystemCronManager,
    system_cron,
    _registry,
    _watchdog_nudged,
    clear_watchdog_nudge,
    _build_tree_status_summary,
)


def test_decorator_registers_handler():
    test_registry: dict[str, SystemCronDef] = {}

    @system_cron("test_cron_1", interval="5m", description="Test cron", registry=test_registry)
    async def my_handler():
        return None

    assert "test_cron_1" in test_registry
    defn = test_registry["test_cron_1"]
    assert defn.name == "test_cron_1"
    assert defn.default_interval == "5m"
    assert defn.description == "Test cron"
    assert defn.handler is my_handler


def test_decorator_rejects_invalid_interval():
    test_registry: dict[str, SystemCronDef] = {}
    with pytest.raises(ValueError, match="Invalid interval"):
        @system_cron("bad", interval="xyz", description="Bad", registry=test_registry)
        async def bad_handler():
            return None


@pytest.mark.asyncio
async def test_manager_start_stop():
    call_count = 0

    async def counting_handler():
        nonlocal call_count
        call_count += 1
        return None

    test_registry = {
        "counter": SystemCronDef(
            name="counter",
            default_interval="1s",
            description="Counter",
            handler=counting_handler,
        ),
    }
    mgr = SystemCronManager(registry=test_registry)
    mgr.start_all()

    await asyncio.sleep(1.5)
    assert call_count >= 1

    await mgr.stop_all()
    final_count = call_count
    await asyncio.sleep(1.5)
    assert call_count == final_count


@pytest.mark.asyncio
async def test_manager_get_all():
    test_registry = {
        "test_a": SystemCronDef(name="test_a", default_interval="1m", description="A", handler=AsyncMock()),
    }
    mgr = SystemCronManager(registry=test_registry)
    infos = mgr.get_all()
    assert len(infos) == 1
    assert infos[0]["name"] == "test_a"
    assert infos[0]["scope"] == "system"
    assert infos[0]["running"] is False


@pytest.mark.asyncio
async def test_manager_start_stop_single():
    test_registry = {
        "single": SystemCronDef(name="single", default_interval="1s", description="S", handler=AsyncMock(return_value=None)),
    }
    mgr = SystemCronManager(registry=test_registry)

    result = mgr.start("single")
    assert result["status"] == "ok"
    infos = mgr.get_all()
    assert infos[0]["running"] is True

    result = mgr.stop("single")
    assert result["status"] == "ok"
    infos = mgr.get_all()
    assert infos[0]["running"] is False

    await mgr.stop_all()


@pytest.mark.asyncio
async def test_manager_update_interval():
    test_registry = {
        "updatable": SystemCronDef(name="updatable", default_interval="1m", description="U", handler=AsyncMock(return_value=None)),
    }
    mgr = SystemCronManager(registry=test_registry)
    mgr.start("updatable")

    result = mgr.update_interval("updatable", "30s")
    assert result["status"] == "ok"
    assert result["interval"] == "30s"

    infos = mgr.get_all()
    assert infos[0]["interval"] == "30s"
    assert infos[0]["running"] is True

    await mgr.stop_all()


@pytest.mark.asyncio
async def test_handler_events_published():
    from onemancompany.core.events import CompanyEvent

    test_event = CompanyEvent(type="test_event", payload={"x": 1}, agent="TEST")

    async def event_handler():
        return [test_event]

    test_registry = {
        "eventer": SystemCronDef(name="eventer", default_interval="1s", description="E", handler=event_handler),
    }
    mgr = SystemCronManager(registry=test_registry)

    with patch("onemancompany.core.events.event_bus") as mock_bus:
        mock_bus.publish = AsyncMock()
        mgr.start_all()
        await asyncio.sleep(1.5)
        await mgr.stop_all()

        mock_bus.publish.assert_called()
        published_events = [call.args[0] for call in mock_bus.publish.call_args_list]
        assert any(e.type == "test_event" for e in published_events)


@pytest.mark.asyncio
async def test_handler_error_does_not_crash_loop():
    call_count = 0

    async def flaky_handler():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        return None

    test_registry = {
        "flaky": SystemCronDef(name="flaky", default_interval="1s", description="F", handler=flaky_handler),
    }
    mgr = SystemCronManager(registry=test_registry)
    mgr.start_all()
    await asyncio.sleep(2.5)
    await mgr.stop_all()

    assert call_count >= 2


# --- Handler tests ---

@pytest.mark.asyncio
async def test_heartbeat_handler_returns_event_on_change():
    with patch("onemancompany.core.heartbeat.run_heartbeat_cycle", new_callable=AsyncMock) as mock_hb:
        mock_hb.return_value = ["emp_001"]
        from onemancompany.core.system_cron import heartbeat_check
        events = await heartbeat_check()
        assert events is not None
        assert len(events) == 1
        assert events[0].type == "state_snapshot"


@pytest.mark.asyncio
async def test_heartbeat_handler_returns_none_when_no_change():
    with patch("onemancompany.core.heartbeat.run_heartbeat_cycle", new_callable=AsyncMock) as mock_hb:
        mock_hb.return_value = []
        from onemancompany.core.system_cron import heartbeat_check
        events = await heartbeat_check()
        assert events is None


@pytest.mark.asyncio
async def test_review_reminder_handler():
    fake_overdue = [{"node_id": "n1", "employee_id": "e1", "waiting_seconds": 600}]
    with patch("onemancompany.core.vessel.scan_overdue_reviews", return_value=fake_overdue):
        from onemancompany.core.system_cron import review_reminder_check
        events = await review_reminder_check()
        assert events is not None
        assert events[0].type == "review_reminder"
        assert events[0].payload["overdue_nodes"] == fake_overdue


@pytest.mark.asyncio
async def test_review_reminder_handler_nothing_overdue():
    with patch("onemancompany.core.vessel.scan_overdue_reviews", return_value=[]):
        from onemancompany.core.system_cron import review_reminder_check
        events = await review_reminder_check()
        assert events is None


@pytest.mark.asyncio
async def test_config_reload_handler_when_idle():
    with patch("onemancompany.core.state.is_idle", return_value=True), \
         patch("onemancompany.core.state.reload_all_from_disk") as mock_reload:
        mock_reload.return_value = {"employees_updated": [], "employees_added": []}
        from onemancompany.core.system_cron import config_reload_check
        events = await config_reload_check()
        assert events is None
        mock_reload.assert_called_once()


@pytest.mark.asyncio
async def test_config_reload_handler_with_updates():
    with patch("onemancompany.core.state.is_idle", return_value=True), \
         patch("onemancompany.core.state.reload_all_from_disk") as mock_reload:
        mock_reload.return_value = {"employees_updated": ["e1"], "employees_added": ["e2"]}
        from onemancompany.core.system_cron import config_reload_check
        events = await config_reload_check()
        assert events is None
        mock_reload.assert_called_once()


@pytest.mark.asyncio
async def test_config_reload_handler_when_busy():
    with patch("onemancompany.core.state.is_idle", return_value=False), \
         patch("onemancompany.core.state.reload_all_from_disk") as mock_reload:
        from onemancompany.core.system_cron import config_reload_check
        events = await config_reload_check()
        assert events is None
        mock_reload.assert_not_called()


def test_list_all_crons_aggregates():
    """list_all_crons returns both system and employee crons."""
    from unittest.mock import MagicMock

    fake_system = [
        {"name": "heartbeat", "interval": "1m", "description": "HB",
         "running": True, "scope": "system", "employee_id": None,
         "last_run": None, "run_count": 0},
    ]
    fake_emp_crons = [
        {"name": "my_cron", "interval": "5m", "task_description": "Do stuff", "running": True},
    ]

    with patch("onemancompany.core.system_cron.system_cron_manager") as mock_mgr, \
         patch("onemancompany.core.automation.EMPLOYEES_DIR") as mock_dir:
        mock_mgr.get_all.return_value = fake_system

        emp_dir = MagicMock()
        emp_dir.is_dir.return_value = True
        emp_dir.name = "emp_001"
        mock_dir.exists.return_value = True
        mock_dir.iterdir.return_value = [emp_dir]

        with patch("onemancompany.core.automation.list_crons", return_value=fake_emp_crons):
            from onemancompany.core.automation import list_all_crons
            result = list_all_crons()

    assert len(result) == 2
    assert result[0]["scope"] == "system"
    assert result[1]["scope"] == "employee"
    assert result[1]["employee_id"] == "emp_001"
    assert result[1]["description"] == "Do stuff"


# ---------------------------------------------------------------------------
# Persisted state
# ---------------------------------------------------------------------------

class TestPersistedState:
    def test_load_persisted_state_no_file(self, tmp_path):
        """_load_persisted_state returns early when file doesn't exist."""
        test_registry = {
            "t": SystemCronDef(name="t", default_interval="1m", description="T", handler=AsyncMock()),
        }
        mgr = SystemCronManager(registry=test_registry)
        mgr._state_path = lambda: tmp_path / "nonexistent.yaml"
        mgr._load_persisted_state()
        assert mgr._disabled == set()

    def test_load_persisted_state_with_data(self, tmp_path):
        """_load_persisted_state restores disabled, enabled, and intervals."""
        state_file = tmp_path / "state.yaml"
        state_file.write_text(
            "disabled:\n- cron_a\nenabled:\n- cron_b\nintervals:\n  cron_c: 2m\n"
        )
        test_registry = {
            "cron_c": SystemCronDef(name="cron_c", default_interval="1m", description="C", handler=AsyncMock()),
        }
        mgr = SystemCronManager(registry=test_registry)
        mgr._state_path = lambda: state_file
        mgr._load_persisted_state()
        assert "cron_a" in mgr._disabled
        assert "cron_b" in mgr._enabled
        assert test_registry["cron_c"].current_interval == "2m"

    def test_load_persisted_state_error(self, tmp_path):
        """_load_persisted_state handles corrupt YAML gracefully."""
        state_file = tmp_path / "state.yaml"
        state_file.write_text(":")  # valid YAML but None
        test_registry = {}
        mgr = SystemCronManager(registry=test_registry)
        mgr._state_path = lambda: state_file
        # Should not raise
        mgr._load_persisted_state()

    def test_load_persisted_state_exception(self, tmp_path):
        """_load_persisted_state catches exceptions during load."""
        state_file = tmp_path / "state.yaml"
        state_file.write_text("valid: yaml")
        test_registry = {}
        mgr = SystemCronManager(registry=test_registry)
        mgr._state_path = lambda: state_file
        with patch("onemancompany.core.system_cron.read_text_utf", side_effect=OSError("read failed")):
            mgr._load_persisted_state()

    def test_persist_state_error(self, tmp_path):
        """_persist_state catches write errors."""
        test_registry = {}
        mgr = SystemCronManager(registry=test_registry)
        mgr._state_path = lambda: tmp_path / "state.yaml"
        with patch("onemancompany.core.system_cron.write_text_utf", side_effect=OSError("write failed")):
            mgr._persist_state()  # Should not raise


# ---------------------------------------------------------------------------
# start_all with disabled-by-default
# ---------------------------------------------------------------------------

class TestStartAllDisabledByDefault:
    @pytest.mark.asyncio
    async def test_disabled_by_default_skipped(self):
        test_registry = {
            "opt_in": SystemCronDef(
                name="opt_in", default_interval="1m", description="Opt-in",
                handler=AsyncMock(return_value=None), enabled_by_default=False,
            ),
            "normal": SystemCronDef(
                name="normal", default_interval="1m", description="Normal",
                handler=AsyncMock(return_value=None),
            ),
        }
        mgr = SystemCronManager(registry=test_registry)
        mgr._persist_state = MagicMock()
        mgr.start_all()
        assert "opt_in" in mgr._disabled
        assert "opt_in" not in mgr._tasks
        assert "normal" in mgr._tasks
        await mgr.stop_all()

    @pytest.mark.asyncio
    async def test_disabled_by_default_but_enabled(self):
        test_registry = {
            "opt_in": SystemCronDef(
                name="opt_in", default_interval="1m", description="Opt-in",
                handler=AsyncMock(return_value=None), enabled_by_default=False,
            ),
        }
        mgr = SystemCronManager(registry=test_registry)
        mgr._enabled.add("opt_in")
        mgr._persist_state = MagicMock()
        mgr.start_all()
        assert "opt_in" in mgr._tasks
        await mgr.stop_all()


# ---------------------------------------------------------------------------
# start/stop edge cases
# ---------------------------------------------------------------------------

class TestStartStopEdgeCases:
    def test_start_unknown(self):
        mgr = SystemCronManager(registry={})
        result = mgr.start("unknown")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_start_replaces_existing(self):
        """Starting an already-running cron cancels the old task."""
        test_registry = {
            "x": SystemCronDef(name="x", default_interval="1s", description="X",
                               handler=AsyncMock(return_value=None)),
        }
        mgr = SystemCronManager(registry=test_registry)
        mgr._persist_state = MagicMock()
        mgr.start("x")
        old_task = mgr._tasks["x"]
        mgr.start("x")
        # old_task should be cancelling (cancel() was called)
        assert old_task.cancelling() or old_task.cancelled()
        await mgr.stop_all()

    def test_update_interval_unknown(self):
        mgr = SystemCronManager(registry={})
        result = mgr.update_interval("unknown", "5m")
        assert result["status"] == "error"

    def test_update_interval_invalid(self):
        test_registry = {
            "x": SystemCronDef(name="x", default_interval="1m", description="X",
                               handler=AsyncMock()),
        }
        mgr = SystemCronManager(registry=test_registry)
        result = mgr.update_interval("x", "invalid_interval")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Talent Market keepalive handler
# ---------------------------------------------------------------------------

class TestTalentMarketKeepalive:
    @pytest.mark.asyncio
    async def test_not_connected(self):
        mock_tm = MagicMock()
        mock_tm.connected = False
        with patch("onemancompany.core.system_cron.talent_market", mock_tm, create=True), \
             patch("onemancompany.agents.recruitment.talent_market", mock_tm):
            from onemancompany.core.system_cron import talent_market_keepalive
            result = await talent_market_keepalive()
        assert result is None

    @pytest.mark.asyncio
    async def test_ping_success(self):
        mock_tm = MagicMock()
        mock_tm.connected = True
        mock_tm._session.send_ping = AsyncMock()
        with patch("onemancompany.agents.recruitment.talent_market", mock_tm), \
             patch("onemancompany.agents.recruitment.start_talent_market", AsyncMock()):
            from onemancompany.core.system_cron import talent_market_keepalive
            result = await talent_market_keepalive()
        assert result is None

    @pytest.mark.asyncio
    async def test_ping_fail_reconnect_success(self):
        mock_tm = MagicMock()
        mock_tm.connected = True
        mock_tm._session.send_ping = AsyncMock(side_effect=ConnectionError("lost"))
        mock_tm._reconnect = AsyncMock()
        with patch("onemancompany.agents.recruitment.talent_market", mock_tm), \
             patch("onemancompany.agents.recruitment.start_talent_market", AsyncMock()):
            from onemancompany.core.system_cron import talent_market_keepalive
            result = await talent_market_keepalive()
        mock_tm._reconnect.assert_called_once()
        assert result is None

    @pytest.mark.asyncio
    async def test_ping_fail_reconnect_fail(self):
        mock_tm = MagicMock()
        mock_tm.connected = True
        mock_tm._session.send_ping = AsyncMock(side_effect=ConnectionError("lost"))
        mock_tm._reconnect = AsyncMock(side_effect=ConnectionError("still lost"))
        with patch("onemancompany.agents.recruitment.talent_market", mock_tm), \
             patch("onemancompany.agents.recruitment.start_talent_market", AsyncMock()):
            from onemancompany.core.system_cron import talent_market_keepalive
            result = await talent_market_keepalive()
        assert result is None


# ---------------------------------------------------------------------------
# Project progress watchdog
# ---------------------------------------------------------------------------

class TestProjectProgressWatchdog:
    @pytest.mark.asyncio
    async def test_no_projects_dir(self):
        with patch("onemancompany.core.config.PROJECTS_DIR", Path("/nonexistent")):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None

    @pytest.mark.asyncio
    async def test_stuck_project_nudges_ea(self, tmp_path):
        """A stuck project (no processing nodes, not all resolved) triggers EA nudge."""
        from onemancompany.core.task_lifecycle import TaskPhase, NodeType
        from onemancompany.core.task_tree import TaskTree

        _watchdog_nudged.clear()

        # Create a tree with a completed (but not accepted) child
        tree = TaskTree(project_id="test-proj")
        root = tree.create_root("00001", "Test project")
        ea_node = tree.add_child(root.id, "00002", "EA node", [])
        child = tree.add_child(root.id, "00010", "Do something", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mock_em = MagicMock()
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.config.EA_ID", "00002"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.task_tree.get_tree_lock", return_value=mock_lock), \
             patch("onemancompany.core.task_tree.save_tree_async"), \
             patch("onemancompany.core.vessel.employee_manager", mock_em), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()

        assert result is not None
        assert len(result) == 1
        assert "test-proj" in _watchdog_nudged
        _watchdog_nudged.clear()

    @pytest.mark.asyncio
    async def test_skip_archived_project(self, tmp_path):
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree(project_id="archived-proj")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "Child", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.project_archive.load_named_project",
                   return_value={"status": "archived"}):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_already_nudged(self, tmp_path):
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree(project_id="nudged-proj")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        _watchdog_nudged.add("nudged-proj")

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None
        _watchdog_nudged.clear()

    @pytest.mark.asyncio
    async def test_skip_processing_project(self, tmp_path):
        """Skip project where a node is currently processing."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree(project_id="busy-proj")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_all_resolved(self, tmp_path):
        """Skip project where all active nodes are resolved."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree(project_id="done-proj")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)
        child.set_status(TaskPhase.ACCEPTED)
        child.set_status(TaskPhase.FINISHED)

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None

    @pytest.mark.asyncio
    async def test_skip_no_ea_node(self, tmp_path):
        """Skip project where no EA node exists."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase

        _watchdog_nudged.clear()
        tree = TaskTree(project_id="no-ea-proj")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)

        # Patch get_ea_node to return None
        tree.get_ea_node = MagicMock(return_value=None)

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None
        _watchdog_nudged.clear()

    @pytest.mark.asyncio
    async def test_skip_active_watchdog_nudge(self, tmp_path):
        """Skip project with unfinished watchdog nudge node (HOLDING, not PROCESSING)."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase, NodeType

        _watchdog_nudged.clear()
        tree = TaskTree(project_id="nudge-active-proj")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)
        nudge = tree.add_child(root.id, "00002", "Nudge", [])
        nudge.node_type = NodeType.WATCHDOG_NUDGE
        # Set to HOLDING (not processing, not resolved) to hit lines 420-422
        nudge.set_status(TaskPhase.PROCESSING)
        nudge.set_status(TaskPhase.HOLDING)

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None
        _watchdog_nudged.clear()

    @pytest.mark.asyncio
    async def test_skip_recently_completed_nudge(self, tmp_path):
        """Skip project with recently completed watchdog nudge (cooldown)."""
        from datetime import datetime
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase, NodeType

        _watchdog_nudged.clear()
        tree = TaskTree(project_id="recent-nudge-proj")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)
        nudge = tree.add_child(root.id, "00002", "Nudge", [])
        nudge.node_type = NodeType.WATCHDOG_NUDGE
        nudge.set_status(TaskPhase.PROCESSING)
        nudge.set_status(TaskPhase.COMPLETED)
        nudge.set_status(TaskPhase.ACCEPTED)
        nudge.set_status(TaskPhase.FINISHED)
        nudge.completed_at = datetime.now().isoformat()  # very recent

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None
        _watchdog_nudged.clear()

    @pytest.mark.asyncio
    async def test_skip_nudge_with_unresolved_children(self, tmp_path):
        """Skip project where watchdog nudge is resolved but has unresolved children."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase, NodeType

        _watchdog_nudged.clear()
        tree = TaskTree(project_id="nudge-child-proj")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)
        # Nudge node is resolved but has unresolved child (HOLDING, not PROCESSING)
        nudge = tree.add_child(root.id, "00002", "Nudge", [])
        nudge.node_type = NodeType.WATCHDOG_NUDGE
        nudge.set_status(TaskPhase.PROCESSING)
        nudge.set_status(TaskPhase.COMPLETED)
        nudge.set_status(TaskPhase.ACCEPTED)
        nudge.set_status(TaskPhase.FINISHED)
        nudge.completed_at = "2020-01-01T00:00:00"  # old enough to pass cooldown
        nudge_child = tree.add_child(nudge.id, "00011", "Sub-nudge", [])
        nudge_child.set_status(TaskPhase.PROCESSING)
        nudge_child.set_status(TaskPhase.HOLDING)  # not resolved, not processing

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None
        _watchdog_nudged.clear()

    @pytest.mark.asyncio
    async def test_skip_nudge_with_invalid_completed_at(self, tmp_path):
        """Handle invalid completed_at gracefully."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase, NodeType

        _watchdog_nudged.clear()
        tree = TaskTree(project_id="bad-date-proj")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)
        nudge = tree.add_child(root.id, "00002", "Nudge", [])
        nudge.node_type = NodeType.WATCHDOG_NUDGE
        nudge.set_status(TaskPhase.PROCESSING)
        nudge.set_status(TaskPhase.COMPLETED)
        nudge.set_status(TaskPhase.ACCEPTED)
        nudge.set_status(TaskPhase.FINISHED)
        nudge.completed_at = "not-a-date"  # Invalid date

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mock_em = MagicMock()
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.config.EA_ID", "00002"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.task_tree.get_tree_lock", return_value=mock_lock), \
             patch("onemancompany.core.task_tree.save_tree_async"), \
             patch("onemancompany.core.vessel.employee_manager", mock_em), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        # Invalid date should be handled gracefully, project still gets nudged
        assert result is not None
        _watchdog_nudged.clear()

    @pytest.mark.asyncio
    async def test_corrupt_tree_skipped(self, tmp_path):
        """Corrupt tree file is skipped gracefully."""
        tree_file = tmp_path / "task_tree.yaml"
        tree_file.write_text("invalid: yaml: content: [")

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", side_effect=ValueError("corrupt")):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_project_id_skipped(self, tmp_path):
        """Tree with empty project_id is skipped."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="")
        root = tree.create_root("00001", "Root")

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None

    @pytest.mark.asyncio
    async def test_no_active_nodes_skipped(self, tmp_path):
        """Tree with no active branch nodes (all inactive) is skipped."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="inactive-proj")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "C", [])
        child.branch_active = False

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path), \
             patch("onemancompany.core.config.TASK_TREE_FILENAME", "task_tree.yaml"), \
             patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            from onemancompany.core.system_cron import project_progress_watchdog
            result = await project_progress_watchdog()
        assert result is None


# ---------------------------------------------------------------------------
# Holding timeout sweep
# ---------------------------------------------------------------------------

class TestHoldingTimeoutSweep:
    @pytest.mark.asyncio
    async def test_holding_timeout_sweep_timed_out(self):
        mock_em = MagicMock()
        entry = MagicMock()
        entry.tree_path = "/tmp/tree.yaml"
        entry.node_id = "n1"
        mock_em._schedule = {"emp1": [entry]}
        mock_em._check_holding_timeout.return_value = True

        mock_tree = MagicMock()
        mock_node = MagicMock()
        mock_tree.get_node.return_value = mock_node

        with patch("onemancompany.core.vessel.employee_manager", mock_em), \
             patch("onemancompany.core.task_tree.get_tree", return_value=mock_tree), \
             patch("onemancompany.core.vessel._trigger_dep_resolution") as mock_dep:
            from onemancompany.core.system_cron import holding_timeout_sweep
            result = await holding_timeout_sweep()

        mock_em.unschedule.assert_called_once_with("emp1", "n1")
        mock_dep.assert_called_once()
        assert result is None

    @pytest.mark.asyncio
    async def test_holding_timeout_sweep_no_timeout(self):
        mock_em = MagicMock()
        entry = MagicMock()
        entry.tree_path = "/tmp/tree.yaml"
        entry.node_id = "n1"
        mock_em._schedule = {"emp1": [entry]}
        mock_em._check_holding_timeout.return_value = False

        with patch("onemancompany.core.vessel.employee_manager", mock_em):
            from onemancompany.core.system_cron import holding_timeout_sweep
            result = await holding_timeout_sweep()

        mock_em.unschedule.assert_not_called()
        assert result is None

    @pytest.mark.asyncio
    async def test_holding_timeout_dep_resolution_error(self):
        mock_em = MagicMock()
        entry = MagicMock()
        entry.tree_path = "/tmp/tree.yaml"
        entry.node_id = "n1"
        mock_em._schedule = {"emp1": [entry]}
        mock_em._check_holding_timeout.return_value = True

        with patch("onemancompany.core.vessel.employee_manager", mock_em), \
             patch("onemancompany.core.task_tree.get_tree", side_effect=RuntimeError("boom")):
            from onemancompany.core.system_cron import holding_timeout_sweep
            result = await holding_timeout_sweep()

        # Should not crash despite error
        assert result is None


# ---------------------------------------------------------------------------
# clear_watchdog_nudge
# ---------------------------------------------------------------------------

def test_clear_watchdog_nudge():
    _watchdog_nudged.add("proj-1")
    clear_watchdog_nudge("proj-1")
    assert "proj-1" not in _watchdog_nudged


def test_clear_watchdog_nudge_nonexistent():
    _watchdog_nudged.discard("nonexistent")
    clear_watchdog_nudge("nonexistent")  # Should not raise


# ---------------------------------------------------------------------------
# schedule_cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_schedule_cleanup():
    mock_em = MagicMock()
    with patch("onemancompany.core.vessel.employee_manager", mock_em):
        from onemancompany.core.system_cron import schedule_cleanup
        result = await schedule_cleanup()
    mock_em.cleanup_orphaned_schedule.assert_called_once()
    assert result is None


# ---------------------------------------------------------------------------
# _build_tree_status_summary
# ---------------------------------------------------------------------------

class TestBuildTreeStatusSummary:
    def test_basic_summary(self):
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.task_lifecycle import TaskPhase

        tree = TaskTree(project_id="p")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "Task A", [])
        c2 = tree.add_child(root.id, "00011", "Task B", [])
        c1.set_status(TaskPhase.PROCESSING)
        c1.set_status(TaskPhase.COMPLETED)

        summary = _build_tree_status_summary(tree)
        assert "[pending]" in summary
        assert "[completed]" in summary
        assert "00010" in summary or "00011" in summary

    def test_empty_tree(self):
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="p")
        tree.create_root("00001", "Root")
        summary = _build_tree_status_summary(tree)
        assert summary == ""

    def test_more_than_five_nodes(self):
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="p")
        root = tree.create_root("00001", "Root")
        for i in range(7):
            tree.add_child(root.id, f"001{i:02d}", f"Task {i}", [])

        summary = _build_tree_status_summary(tree)
        assert "... and 2 more" in summary


# ---------------------------------------------------------------------------
# _loop error tracking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_cancelled_in_handler():
    """CancelledError during handler execution is re-raised to stop loop."""
    async def cancel_handler():
        raise asyncio.CancelledError()

    defn = SystemCronDef(
        name="cancel_inner", default_interval="1s", description="C",
        handler=cancel_handler,
    )
    test_registry = {"cancel_inner": defn}
    mgr = SystemCronManager(registry=test_registry)
    mgr._persist_state = MagicMock()
    mgr.start("cancel_inner")
    await asyncio.sleep(1.5)
    task = mgr._tasks.get("cancel_inner")
    # Task should be done because CancelledError propagated
    assert task is None or task.done()
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_loop_records_error():
    """Handler error sets last_error and increments run_count."""
    call_count = 0

    async def fail_handler():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("handler error")

    defn = SystemCronDef(
        name="err", default_interval="1s", description="E", handler=fail_handler,
    )
    test_registry = {"err": defn}
    mgr = SystemCronManager(registry=test_registry)
    mgr._persist_state = MagicMock()
    mgr.start("err")
    await asyncio.sleep(1.5)
    await mgr.stop_all()

    assert defn.last_error == "handler error"
    assert defn.run_count >= 1
