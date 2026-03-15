"""Tests for the system cron registry and manager."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from onemancompany.core.system_cron import (
    SystemCronDef,
    SystemCronManager,
    system_cron,
    _registry,
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
async def test_config_reload_handler_when_busy():
    with patch("onemancompany.core.state.is_idle", return_value=False), \
         patch("onemancompany.core.state.reload_all_from_disk") as mock_reload:
        from onemancompany.core.system_cron import config_reload_check
        events = await config_reload_check()
        assert events is None
        mock_reload.assert_not_called()
