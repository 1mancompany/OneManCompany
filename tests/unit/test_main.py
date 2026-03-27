"""Unit tests for main.py — FastAPI entrypoint, lifespan, state persistence."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany import main as main_mod


# ---------------------------------------------------------------------------
# NoCacheStaticMiddleware
# ---------------------------------------------------------------------------


class TestNoCacheStaticMiddleware:
    @pytest.mark.asyncio
    async def test_js_files_get_no_cache_headers(self):
        middleware = main_mod.NoCacheStaticMiddleware(app=MagicMock())
        request = MagicMock()
        request.url.path = "/app.js"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert result.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"

    @pytest.mark.asyncio
    async def test_css_files_get_no_cache_headers(self):
        middleware = main_mod.NoCacheStaticMiddleware(app=MagicMock())
        request = MagicMock()
        request.url.path = "/style.css"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert "Cache-Control" in result.headers

    @pytest.mark.asyncio
    async def test_html_files_get_no_cache_headers(self):
        middleware = main_mod.NoCacheStaticMiddleware(app=MagicMock())
        request = MagicMock()
        request.url.path = "/index.html"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert "Cache-Control" in result.headers

    @pytest.mark.asyncio
    async def test_root_path_gets_no_cache_headers(self):
        middleware = main_mod.NoCacheStaticMiddleware(app=MagicMock())
        request = MagicMock()
        request.url.path = "/"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert "Cache-Control" in result.headers

    @pytest.mark.asyncio
    async def test_api_paths_no_cache_header(self):
        middleware = main_mod.NoCacheStaticMiddleware(app=MagicMock())
        request = MagicMock()
        request.url.path = "/api/employees"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await middleware.dispatch(request, call_next)
        assert "Cache-Control" not in result.headers


# ---------------------------------------------------------------------------
# _save_ephemeral_state
# ---------------------------------------------------------------------------


class TestSaveEphemeralState:
    def test_save_delegates_to_snapshot_harness(self, monkeypatch):
        """_save_ephemeral_state delegates to core.snapshot.save_snapshot."""
        mock_save = MagicMock()
        monkeypatch.setattr("onemancompany.core.snapshot.save_snapshot", mock_save)

        main_mod._save_ephemeral_state()
        mock_save.assert_called_once()

    def test_restore_delegates_to_snapshot_harness(self, monkeypatch):
        """_restore_ephemeral_state delegates to core.snapshot.restore_snapshot."""
        mock_restore = MagicMock()
        monkeypatch.setattr("onemancompany.core.snapshot.restore_snapshot", mock_restore)

        main_mod._restore_ephemeral_state()
        mock_restore.assert_called_once()


# ---------------------------------------------------------------------------
# _restore_ephemeral_state
# ---------------------------------------------------------------------------


class TestRestoreEphemeralState:
    def test_restore_delegates_to_snapshot_harness(self, monkeypatch):
        """_restore_ephemeral_state delegates to core.snapshot.restore_snapshot."""
        mock_restore = MagicMock()
        monkeypatch.setattr("onemancompany.core.snapshot.restore_snapshot", mock_restore)

        main_mod._restore_ephemeral_state()
        mock_restore.assert_called_once()


# ---------------------------------------------------------------------------
# _start_file_watcher
# ---------------------------------------------------------------------------


class TestStartFileWatcher:
    @pytest.mark.asyncio
    async def test_file_watcher_starts_and_stops_hot_reload_on(self, capsys):
        """Observer is started, handlers registered, cleanup on cancel."""
        await _run_watcher_and_capture(hot_reload_enabled=True)
        captured = capsys.readouterr()
        assert "[hot-reload] Watching" in captured.out
        assert "hot_reload: true" in captured.out

    @pytest.mark.asyncio
    async def test_file_watcher_hot_reload_disabled(self, capsys):
        await _run_watcher_and_capture(hot_reload_enabled=False)
        captured = capsys.readouterr()
        assert "[hot-reload] Watching" in captured.out
        assert "hot_reload: true" not in captured.out


async def _run_watcher_and_capture(request_reload_return=None, request_reload_side_effect=None,
                                    hot_reload_enabled=False, config_path=None):
    """Helper: run _start_file_watcher briefly, capture the handler objects."""
    from watchdog.observers import Observer

    if config_path is None:
        config_path = Path("/fake/config.yaml")

    captured_handlers = []
    reload_kwargs = {}
    if request_reload_side_effect:
        reload_kwargs["side_effect"] = request_reload_side_effect
    else:
        reload_kwargs["return_value"] = request_reload_return or {}

    with patch("onemancompany.core.config.APP_CONFIG_PATH", config_path):
        with patch("onemancompany.core.config.COMPANY_DIR", Path("/fake/company")):
            with patch("onemancompany.core.config.is_hot_reload_enabled", return_value=hot_reload_enabled):
                with patch("onemancompany.core.state.request_reload", **reload_kwargs):
                    def capture_schedule(self, handler, path, **kw):
                        captured_handlers.append(handler)

                    with patch.object(Observer, "schedule", capture_schedule):
                        with patch.object(Observer, "start", lambda self: None):
                            with patch.object(Observer, "stop", lambda self: None):
                                with patch.object(Observer, "join", lambda self, **kw: None):
                                    task = asyncio.create_task(main_mod._start_file_watcher())
                                    await asyncio.sleep(0.05)
                                    task.cancel()
                                    try:
                                        await task
                                    except asyncio.CancelledError:
                                        pass

    return captured_handlers


class TestReloadHandlerInner:
    """Test _ReloadHandler inner class via captured handler references."""

    @pytest.mark.asyncio
    async def test_on_modified_yaml_triggers_schedule_reload(self):
        """Modifying a .yaml file triggers _schedule_reload."""
        captured_handlers = await _run_watcher_and_capture()

        assert len(captured_handlers) >= 1
        reload_handler = captured_handlers[0]

        # Directory event ignored
        dir_event = MagicMock(is_directory=True)
        reload_handler.on_modified(dir_event)

        # Non-watched extension ignored
        txt_event = MagicMock(is_directory=False, src_path="/fake/company/file.txt")
        reload_handler.on_modified(txt_event)

        # .yaml file triggers schedule_reload
        yaml_event = MagicMock(is_directory=False, src_path="/fake/company/profile.yaml")
        reload_handler.on_modified(yaml_event)
        assert reload_handler._pending is not None
        reload_handler._pending.cancel()

    @pytest.mark.asyncio
    async def test_on_created_yaml_triggers_schedule_reload(self):
        """Creating a .yaml file triggers _schedule_reload."""
        captured_handlers = await _run_watcher_and_capture()
        reload_handler = captured_handlers[0]

        # Directory event ignored
        reload_handler.on_created(MagicMock(is_directory=True))

        # Non-yaml ignored
        reload_handler.on_created(MagicMock(is_directory=False, src_path="/fake/company/file.txt"))

        # .md file triggers
        reload_handler.on_created(MagicMock(is_directory=False, src_path="/fake/company/guide.md"))
        assert reload_handler._pending is not None
        reload_handler._pending.cancel()

    @pytest.mark.asyncio
    async def test_schedule_reload_debounce(self):
        """Multiple rapid events cancel previous timer and reschedule."""
        captured_handlers = await _run_watcher_and_capture()
        reload_handler = captured_handlers[0]

        event1 = MagicMock(is_directory=False, src_path="/fake/company/a.yaml")
        reload_handler.on_modified(event1)
        first_pending = reload_handler._pending

        event2 = MagicMock(is_directory=False, src_path="/fake/company/b.yml")
        reload_handler.on_modified(event2)
        second_pending = reload_handler._pending

        assert first_pending is not second_pending
        second_pending.cancel()

    @pytest.mark.asyncio
    async def test_do_reload_deferred(self, capsys):
        """_do_reload prints deferred message when agents are busy."""
        captured_handlers = await _run_watcher_and_capture(
            request_reload_return={"status": "deferred"}
        )
        reload_handler = captured_handlers[0]
        reload_handler._pending = MagicMock()
        reload_handler._do_reload()

        captured = capsys.readouterr()
        assert "Deferred" in captured.out

    @pytest.mark.asyncio
    async def test_do_reload_with_updates(self, capsys):
        """_do_reload prints updated/added counts."""
        captured_handlers = await _run_watcher_and_capture(
            request_reload_return={
                "employees_updated": ["e1"], "employees_added": ["e2"],
                "config_reloaded": True,
            }
        )
        reload_handler = captured_handlers[0]
        reload_handler._do_reload()

        captured = capsys.readouterr()
        assert "Reloaded from disk" in captured.out
        assert "config.yaml reloaded" in captured.out
        assert reload_handler._pending is None

    @pytest.mark.asyncio
    async def test_do_reload_with_no_changes(self, capsys):
        """_do_reload with no updated/added produces no output."""
        captured_handlers = await _run_watcher_and_capture(
            request_reload_return={"employees_updated": [], "employees_added": []}
        )
        reload_handler = captured_handlers[0]
        reload_handler._do_reload()

        captured = capsys.readouterr()
        assert "Reloaded from disk" not in captured.out

    @pytest.mark.asyncio
    async def test_do_reload_exception(self, capsys):
        """_do_reload handles exceptions gracefully."""
        captured_handlers = await _run_watcher_and_capture(
            request_reload_side_effect=RuntimeError("oops")
        )
        reload_handler = captured_handlers[0]
        reload_handler._do_reload()

        captured = capsys.readouterr()
        assert "[hot-reload] Error" in captured.out


class TestConfigReloadHandler:
    """Test _ConfigReloadHandler via captured handler references."""

    @pytest.mark.asyncio
    async def test_config_handler_triggers_on_config_change(self):
        """Config handler triggers reload when config.yaml changes and hot_reload is on."""
        config_path = Path("/fake/config.yaml")
        captured_handlers = await _run_watcher_and_capture(
            hot_reload_enabled=True, config_path=config_path
        )

        assert len(captured_handlers) == 2
        config_handler = captured_handlers[1]

        # Directory events ignored
        config_handler.on_modified(MagicMock(is_directory=True))

        # Non-config file ignored
        config_handler.on_modified(MagicMock(is_directory=False, src_path="/fake/other.yaml"))

        # Config file with hot_reload enabled triggers reload
        config_handler.on_modified(MagicMock(is_directory=False, src_path=str(config_path)))

        reload_handler = captured_handlers[0]
        assert reload_handler._pending is not None
        reload_handler._pending.cancel()

    @pytest.mark.asyncio
    async def test_config_handler_noop_when_hot_reload_off(self):
        """Config handler does nothing when hot_reload is disabled."""
        config_path = Path("/fake/config.yaml")

        from watchdog.observers import Observer

        captured_handlers = []
        hot_reload_flag = [True]

        def mock_hot_reload():
            return hot_reload_flag[0]

        with patch("onemancompany.core.config.APP_CONFIG_PATH", config_path):
            with patch("onemancompany.core.config.COMPANY_DIR", Path("/fake/company")):
                with patch("onemancompany.core.config.is_hot_reload_enabled", mock_hot_reload):
                    with patch("onemancompany.core.state.request_reload", return_value={}):
                        def capture_schedule(self, handler, path, **kw):
                            captured_handlers.append(handler)

                        with patch.object(Observer, "schedule", capture_schedule):
                            with patch.object(Observer, "start", lambda self: None):
                                with patch.object(Observer, "stop", lambda self: None):
                                    with patch.object(Observer, "join", lambda self, **kw: None):
                                        task = asyncio.create_task(main_mod._start_file_watcher())
                                        await asyncio.sleep(0.05)
                                        task.cancel()
                                        try:
                                            await task
                                        except asyncio.CancelledError:
                                            pass

        hot_reload_flag[0] = False

        config_handler = captured_handlers[1]
        reload_handler = captured_handlers[0]

        config_handler.on_modified(MagicMock(is_directory=False, src_path=str(config_path)))
        assert reload_handler._pending is None


# ---------------------------------------------------------------------------
# lifespan
# ---------------------------------------------------------------------------


class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_startup_and_shutdown(self, monkeypatch):
        """Test full lifespan context manager: startup -> yield -> shutdown."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        # Mock all startup dependencies
        mock_load_assets = MagicMock()
        monkeypatch.setattr("onemancompany.agents.coo_agent._load_assets_from_disk", mock_load_assets)

        mock_start_sandbox = MagicMock()
        monkeypatch.setattr("onemancompany.tools.sandbox.start_sandbox_server", mock_start_sandbox)

        monkeypatch.setattr(main_mod, "_restore_ephemeral_state", MagicMock())
        monkeypatch.setattr(main_mod, "_save_ephemeral_state", MagicMock())

        mock_register_founding = MagicMock()
        mock_register_agent = MagicMock()
        mock_register_self_hosted = MagicMock()
        mock_start_all = AsyncMock()
        mock_stop_all = AsyncMock()
        monkeypatch.setattr("onemancompany.core.vessel.register_founding_employee", mock_register_founding)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_agent", mock_register_agent)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_self_hosted", mock_register_self_hosted)
        monkeypatch.setattr("onemancompany.core.agent_loop.start_all_loops", mock_start_all)
        monkeypatch.setattr("onemancompany.core.agent_loop.stop_all_loops", mock_stop_all)

        mock_start_tm = AsyncMock()
        mock_stop_tm = AsyncMock()
        monkeypatch.setattr("onemancompany.agents.recruitment.start_talent_market", mock_start_tm)
        monkeypatch.setattr("onemancompany.agents.recruitment.stop_talent_market", mock_stop_tm)

        # Mock employee classes
        monkeypatch.setattr("onemancompany.agents.hr_agent.HRAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.coo_agent.COOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.ea_agent.EAAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.cso_agent.CSOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.base.EmployeeAgent", MagicMock, raising=False)

        # Mock company_state.employees
        mock_cs = MagicMock()
        mock_cs.employees = {}  # no non-founding employees
        monkeypatch.setattr("onemancompany.core.state.company_state", mock_cs)

        # Mock employee_configs
        monkeypatch.setattr("onemancompany.core.config.employee_configs", {})

        # Mock ws_manager
        mock_broadcaster = AsyncMock()
        mock_ws_manager = MagicMock()
        mock_ws_manager.event_broadcaster = mock_broadcaster
        monkeypatch.setattr(main_mod, "ws_manager", mock_ws_manager)

        # Mock background task targets to exit immediately
        async def mock_watcher():
            await asyncio.sleep(100)

        monkeypatch.setattr(main_mod, "_start_file_watcher", mock_watcher)
        monkeypatch.setattr("onemancompany.core.system_cron.system_cron_manager", MagicMock(start_all=MagicMock(), stop_all=AsyncMock()))

        # Mock restore_persisted_tasks to avoid scanning real project files
        monkeypatch.setattr(
            "onemancompany.core.vessel.EmployeeManager.restore_persisted_tasks",
            MagicMock(return_value=0),
        )

        # Mock shutdown dependencies
        mock_cleanup_sandbox = AsyncMock()
        mock_stop_sandbox = MagicMock()
        monkeypatch.setattr("onemancompany.tools.sandbox.cleanup_sandbox", mock_cleanup_sandbox)
        monkeypatch.setattr("onemancompany.tools.sandbox.stop_sandbox_server", mock_stop_sandbox)

        async with main_mod.lifespan(mock_app):
            # Verify startup was called
            mock_load_assets.assert_called_once()
            mock_start_sandbox.assert_called_once()
            mock_start_tm.assert_awaited_once()
            mock_start_all.assert_awaited_once()
            assert mock_register_founding.call_count == 4  # HR, COO, EA, CSO

        # Verify shutdown was called
        mock_stop_all.assert_awaited_once()
        mock_stop_tm.assert_awaited_once()
        main_mod._save_ephemeral_state.assert_called_once()
        mock_cleanup_sandbox.assert_awaited_once()
        mock_stop_sandbox.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_registers_non_founding_employees(self, monkeypatch):
        """Test that non-founding, on-site, company-hosted employees are registered."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        monkeypatch.setattr("onemancompany.agents.coo_agent._load_assets_from_disk", MagicMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.start_sandbox_server", MagicMock())
        monkeypatch.setattr(main_mod, "_restore_ephemeral_state", MagicMock())
        monkeypatch.setattr(main_mod, "_save_ephemeral_state", MagicMock())

        mock_register_founding = MagicMock()
        mock_register_agent = MagicMock()
        mock_register_self_hosted = MagicMock()
        monkeypatch.setattr("onemancompany.core.vessel.register_founding_employee", mock_register_founding)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_agent", mock_register_agent)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_self_hosted", mock_register_self_hosted)
        monkeypatch.setattr("onemancompany.core.agent_loop.start_all_loops", AsyncMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.stop_all_loops", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.recruitment.start_talent_market", AsyncMock())
        monkeypatch.setattr("onemancompany.agents.recruitment.stop_talent_market", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.hr_agent.HRAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.coo_agent.COOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.ea_agent.EAAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.cso_agent.CSOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.base.EmployeeAgent", MagicMock, raising=False)

        # Create a non-founding employee
        emp = MagicMock()
        emp.name = "Alice"
        emp.level = 1
        emp.remote = False

        mock_cs = MagicMock()
        mock_cs.employees = {"00010": emp}
        monkeypatch.setattr("onemancompany.core.state.company_state", mock_cs)

        # company-hosted config
        cfg = MagicMock()
        cfg.hosting = "company"
        monkeypatch.setattr("onemancompany.core.config.employee_configs", {"00010": cfg})

        mock_ws_manager = MagicMock()
        mock_ws_manager.event_broadcaster = AsyncMock()
        monkeypatch.setattr(main_mod, "ws_manager", mock_ws_manager)

        async def mock_noop():
            await asyncio.sleep(100)

        monkeypatch.setattr(main_mod, "_start_file_watcher", mock_noop)
        monkeypatch.setattr("onemancompany.core.system_cron.system_cron_manager", MagicMock(start_all=MagicMock(), stop_all=AsyncMock()))

        monkeypatch.setattr("onemancompany.tools.sandbox.cleanup_sandbox", AsyncMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.stop_sandbox_server", MagicMock())

        async with main_mod.lifespan(mock_app):
            pass

        # 4 founding via register_founding_employee, 1 non-founding via register_agent
        assert mock_register_founding.call_count == 4
        assert mock_register_agent.call_count == 1

    @pytest.mark.asyncio
    async def test_lifespan_registers_self_hosted_employees(self, monkeypatch):
        """Test that self-hosted employees get register_self_hosted."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        monkeypatch.setattr("onemancompany.agents.coo_agent._load_assets_from_disk", MagicMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.start_sandbox_server", MagicMock())
        monkeypatch.setattr(main_mod, "_restore_ephemeral_state", MagicMock())
        monkeypatch.setattr(main_mod, "_save_ephemeral_state", MagicMock())

        mock_register_founding = MagicMock()
        mock_register_agent = MagicMock()
        mock_register_self_hosted = MagicMock()
        monkeypatch.setattr("onemancompany.core.vessel.register_founding_employee", mock_register_founding)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_agent", mock_register_agent)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_self_hosted", mock_register_self_hosted)
        monkeypatch.setattr("onemancompany.core.agent_loop.start_all_loops", AsyncMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.stop_all_loops", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.recruitment.start_talent_market", AsyncMock())
        monkeypatch.setattr("onemancompany.agents.recruitment.stop_talent_market", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.hr_agent.HRAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.coo_agent.COOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.ea_agent.EAAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.cso_agent.CSOAgent", MagicMock, raising=False)

        # Self-hosted employee
        emp = MagicMock()
        emp.name = "SelfHosted Bob"
        emp.level = 1
        emp.remote = False

        mock_cs = MagicMock()
        mock_cs.employees = {"00011": emp}
        monkeypatch.setattr("onemancompany.core.state.company_state", mock_cs)

        cfg = MagicMock()
        cfg.hosting = "self"
        monkeypatch.setattr("onemancompany.core.config.employee_configs", {"00011": cfg})

        mock_ws_manager = MagicMock()
        mock_ws_manager.event_broadcaster = AsyncMock()
        monkeypatch.setattr(main_mod, "ws_manager", mock_ws_manager)

        async def mock_noop():
            await asyncio.sleep(100)

        monkeypatch.setattr(main_mod, "_start_file_watcher", mock_noop)
        monkeypatch.setattr("onemancompany.core.system_cron.system_cron_manager", MagicMock(start_all=MagicMock(), stop_all=AsyncMock()))

        monkeypatch.setattr("onemancompany.tools.sandbox.cleanup_sandbox", AsyncMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.stop_sandbox_server", MagicMock())

        async with main_mod.lifespan(mock_app):
            pass

        mock_register_self_hosted.assert_called_once()
        assert mock_register_self_hosted.call_args[0][0] == "00011"

    @pytest.mark.asyncio
    async def test_lifespan_skips_founding_employees(self, monkeypatch):
        """Founding employee IDs should not be registered as non-founding."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        monkeypatch.setattr("onemancompany.agents.coo_agent._load_assets_from_disk", MagicMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.start_sandbox_server", MagicMock())
        monkeypatch.setattr(main_mod, "_restore_ephemeral_state", MagicMock())
        monkeypatch.setattr(main_mod, "_save_ephemeral_state", MagicMock())

        mock_register_founding = MagicMock()
        mock_register_agent = MagicMock()
        mock_register_self_hosted = MagicMock()
        monkeypatch.setattr("onemancompany.core.vessel.register_founding_employee", mock_register_founding)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_agent", mock_register_agent)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_self_hosted", mock_register_self_hosted)
        monkeypatch.setattr("onemancompany.core.agent_loop.start_all_loops", AsyncMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.stop_all_loops", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.recruitment.start_talent_market", AsyncMock())
        monkeypatch.setattr("onemancompany.agents.recruitment.stop_talent_market", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.hr_agent.HRAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.coo_agent.COOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.ea_agent.EAAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.cso_agent.CSOAgent", MagicMock, raising=False)

        # Founding employee with id in founding_ids set
        emp = MagicMock()
        emp.name = "HR"
        emp.level = 1
        emp.remote = False

        mock_cs = MagicMock()
        mock_cs.employees = {"00002": emp}  # HR_ID is a founding id
        monkeypatch.setattr("onemancompany.core.state.company_state", mock_cs)
        monkeypatch.setattr("onemancompany.core.config.employee_configs", {})

        mock_ws_manager = MagicMock()
        mock_ws_manager.event_broadcaster = AsyncMock()
        monkeypatch.setattr(main_mod, "ws_manager", mock_ws_manager)

        async def mock_noop():
            await asyncio.sleep(100)

        monkeypatch.setattr(main_mod, "_start_file_watcher", mock_noop)
        monkeypatch.setattr("onemancompany.core.system_cron.system_cron_manager", MagicMock(start_all=MagicMock(), stop_all=AsyncMock()))

        monkeypatch.setattr("onemancompany.tools.sandbox.cleanup_sandbox", AsyncMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.stop_sandbox_server", MagicMock())

        async with main_mod.lifespan(mock_app):
            pass

        # Only the 4 founding registrations (HR, COO, EA, CSO), not the extra one
        assert mock_register_founding.call_count == 4
        assert mock_register_agent.call_count == 0

    @pytest.mark.asyncio
    async def test_lifespan_skips_high_level_employees(self, monkeypatch):
        """Employees at or above FOUNDING_LEVEL should be skipped."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        monkeypatch.setattr("onemancompany.agents.coo_agent._load_assets_from_disk", MagicMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.start_sandbox_server", MagicMock())
        monkeypatch.setattr(main_mod, "_restore_ephemeral_state", MagicMock())
        monkeypatch.setattr(main_mod, "_save_ephemeral_state", MagicMock())

        mock_register_founding = MagicMock()
        mock_register_agent = MagicMock()
        monkeypatch.setattr("onemancompany.core.vessel.register_founding_employee", mock_register_founding)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_agent", mock_register_agent)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_self_hosted", MagicMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.start_all_loops", AsyncMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.stop_all_loops", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.recruitment.start_talent_market", AsyncMock())
        monkeypatch.setattr("onemancompany.agents.recruitment.stop_talent_market", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.hr_agent.HRAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.coo_agent.COOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.ea_agent.EAAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.cso_agent.CSOAgent", MagicMock, raising=False)

        # High-level employee (should be skipped)
        emp = MagicMock()
        emp.name = "High"
        emp.level = 5  # >= FOUNDING_LEVEL(4)
        emp.remote = False

        mock_cs = MagicMock()
        mock_cs.employees = {"00020": emp}
        monkeypatch.setattr("onemancompany.core.state.company_state", mock_cs)
        monkeypatch.setattr("onemancompany.core.config.employee_configs", {})

        mock_ws_manager = MagicMock()
        mock_ws_manager.event_broadcaster = AsyncMock()
        monkeypatch.setattr(main_mod, "ws_manager", mock_ws_manager)

        async def mock_noop():
            await asyncio.sleep(100)

        monkeypatch.setattr(main_mod, "_start_file_watcher", mock_noop)
        monkeypatch.setattr("onemancompany.core.system_cron.system_cron_manager", MagicMock(start_all=MagicMock(), stop_all=AsyncMock()))

        monkeypatch.setattr("onemancompany.tools.sandbox.cleanup_sandbox", AsyncMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.stop_sandbox_server", MagicMock())

        async with main_mod.lifespan(mock_app):
            pass

        # Only 4 founding, not 5
        assert mock_register_founding.call_count == 4
        assert mock_register_agent.call_count == 0

    @pytest.mark.asyncio
    async def test_lifespan_skips_remote_employees(self, monkeypatch):
        """Remote employees should be skipped."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        monkeypatch.setattr("onemancompany.agents.coo_agent._load_assets_from_disk", MagicMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.start_sandbox_server", MagicMock())
        monkeypatch.setattr(main_mod, "_restore_ephemeral_state", MagicMock())
        monkeypatch.setattr(main_mod, "_save_ephemeral_state", MagicMock())

        mock_register_founding = MagicMock()
        mock_register_agent = MagicMock()
        monkeypatch.setattr("onemancompany.core.vessel.register_founding_employee", mock_register_founding)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_agent", mock_register_agent)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_self_hosted", MagicMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.start_all_loops", AsyncMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.stop_all_loops", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.recruitment.start_talent_market", AsyncMock())
        monkeypatch.setattr("onemancompany.agents.recruitment.stop_talent_market", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.hr_agent.HRAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.coo_agent.COOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.ea_agent.EAAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.cso_agent.CSOAgent", MagicMock, raising=False)

        # Remote employee
        emp = MagicMock()
        emp.name = "Remote"
        emp.level = 1
        emp.remote = True

        mock_cs = MagicMock()
        mock_cs.employees = {"00020": emp}
        monkeypatch.setattr("onemancompany.core.state.company_state", mock_cs)
        monkeypatch.setattr("onemancompany.core.config.employee_configs", {})

        mock_ws_manager = MagicMock()
        mock_ws_manager.event_broadcaster = AsyncMock()
        monkeypatch.setattr(main_mod, "ws_manager", mock_ws_manager)

        async def mock_noop():
            await asyncio.sleep(100)

        monkeypatch.setattr(main_mod, "_start_file_watcher", mock_noop)
        monkeypatch.setattr("onemancompany.core.system_cron.system_cron_manager", MagicMock(start_all=MagicMock(), stop_all=AsyncMock()))

        monkeypatch.setattr("onemancompany.tools.sandbox.cleanup_sandbox", AsyncMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.stop_sandbox_server", MagicMock())

        async with main_mod.lifespan(mock_app):
            pass

        # Only 4 founding
        assert mock_register_founding.call_count == 4
        assert mock_register_agent.call_count == 0

    @pytest.mark.asyncio
    async def test_lifespan_employee_with_no_config(self, monkeypatch):
        """Non-founding employee with no config entry should register as LangChain agent."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        monkeypatch.setattr("onemancompany.agents.coo_agent._load_assets_from_disk", MagicMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.start_sandbox_server", MagicMock())
        monkeypatch.setattr(main_mod, "_restore_ephemeral_state", MagicMock())
        monkeypatch.setattr(main_mod, "_save_ephemeral_state", MagicMock())

        mock_register_founding = MagicMock()
        mock_register_agent = MagicMock()
        mock_register_self_hosted = MagicMock()
        monkeypatch.setattr("onemancompany.core.vessel.register_founding_employee", mock_register_founding)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_agent", mock_register_agent)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_self_hosted", mock_register_self_hosted)
        monkeypatch.setattr("onemancompany.core.agent_loop.start_all_loops", AsyncMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.stop_all_loops", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.recruitment.start_talent_market", AsyncMock())
        monkeypatch.setattr("onemancompany.agents.recruitment.stop_talent_market", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.hr_agent.HRAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.coo_agent.COOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.ea_agent.EAAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.cso_agent.CSOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.base.EmployeeAgent", MagicMock, raising=False)

        emp = MagicMock()
        emp.name = "NoConfig"
        emp.level = 1
        emp.remote = False

        mock_cs = MagicMock()
        mock_cs.employees = {"00030": emp}
        monkeypatch.setattr("onemancompany.core.state.company_state", mock_cs)
        # No config for this employee
        monkeypatch.setattr("onemancompany.core.config.employee_configs", {})

        mock_ws_manager = MagicMock()
        mock_ws_manager.event_broadcaster = AsyncMock()
        monkeypatch.setattr(main_mod, "ws_manager", mock_ws_manager)

        async def mock_noop():
            await asyncio.sleep(100)

        monkeypatch.setattr(main_mod, "_start_file_watcher", mock_noop)
        monkeypatch.setattr("onemancompany.core.system_cron.system_cron_manager", MagicMock(start_all=MagicMock(), stop_all=AsyncMock()))

        monkeypatch.setattr("onemancompany.tools.sandbox.cleanup_sandbox", AsyncMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.stop_sandbox_server", MagicMock())

        async with main_mod.lifespan(mock_app):
            pass

        # _cfg is None for non-founding, goes to register_agent
        # 4 founding via register_founding_employee, 1 non-founding via register_agent
        assert mock_register_founding.call_count == 4
        assert mock_register_agent.call_count == 1
        mock_register_self_hosted.assert_not_called()


class TestLifespanGatherCancelledError:
    """Cover the except asyncio.CancelledError: pass branch in lifespan shutdown."""

    @pytest.mark.asyncio
    async def test_lifespan_gather_cancelled_error(self, monkeypatch):
        """When asyncio.gather raises CancelledError, lifespan catches it."""
        mock_app = MagicMock()
        mock_app.state = MagicMock()

        monkeypatch.setattr("onemancompany.agents.coo_agent._load_assets_from_disk", MagicMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.start_sandbox_server", MagicMock())
        monkeypatch.setattr(main_mod, "_restore_ephemeral_state", MagicMock())
        monkeypatch.setattr(main_mod, "_save_ephemeral_state", MagicMock())

        monkeypatch.setattr("onemancompany.core.agent_loop.register_agent", MagicMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.register_self_hosted", MagicMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.start_all_loops", AsyncMock())
        monkeypatch.setattr("onemancompany.core.agent_loop.stop_all_loops", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.recruitment.start_talent_market", AsyncMock())
        monkeypatch.setattr("onemancompany.agents.recruitment.stop_talent_market", AsyncMock())

        monkeypatch.setattr("onemancompany.agents.hr_agent.HRAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.coo_agent.COOAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.ea_agent.EAAgent", MagicMock, raising=False)
        monkeypatch.setattr("onemancompany.agents.cso_agent.CSOAgent", MagicMock, raising=False)

        mock_cs = MagicMock()
        mock_cs.employees = {}
        monkeypatch.setattr("onemancompany.core.state.company_state", mock_cs)
        monkeypatch.setattr("onemancompany.core.config.employee_configs", {})

        mock_ws_manager = MagicMock()
        mock_ws_manager.event_broadcaster = AsyncMock()
        monkeypatch.setattr(main_mod, "ws_manager", mock_ws_manager)

        async def mock_noop():
            await asyncio.sleep(100)

        monkeypatch.setattr(main_mod, "_start_file_watcher", mock_noop)
        monkeypatch.setattr("onemancompany.core.system_cron.system_cron_manager", MagicMock(start_all=MagicMock(), stop_all=AsyncMock()))

        monkeypatch.setattr("onemancompany.tools.sandbox.cleanup_sandbox", AsyncMock())
        monkeypatch.setattr("onemancompany.tools.sandbox.stop_sandbox_server", MagicMock())

        # Make asyncio.gather raise CancelledError
        original_gather = asyncio.gather

        async def mock_gather(*coros, **kwargs):
            raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "gather", mock_gather)

        # Should not raise — the except CancelledError: pass catches it
        async with main_mod.lifespan(mock_app):
            pass


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_calls_uvicorn(self, monkeypatch):
        mock_settings = MagicMock()
        mock_settings.host = "0.0.0.0"
        mock_settings.port = 9000

        monkeypatch.setattr("onemancompany.core.config.settings", mock_settings)
        monkeypatch.setattr("onemancompany.core.config.SOURCE_ROOT", Path("/fake/root"))

        mock_uvicorn_run = MagicMock()
        monkeypatch.setattr(main_mod, "uvicorn", MagicMock(run=mock_uvicorn_run))

        main_mod.run()

        mock_uvicorn_run.assert_called_once()
        call_kwargs = mock_uvicorn_run.call_args
        assert call_kwargs[1]["host"] == "0.0.0.0"
        assert call_kwargs[1]["port"] == 9000


class TestMainModule:
    def test_main_guard_exists(self):
        """The if __name__ == '__main__' guard is present in source code."""
        # Line 371 is the standard `if __name__ == "__main__": run()` guard.
        # It cannot be tested via normal import (since __name__ != "__main__").
        # Just verify the source code contains it.
        source_path = Path(main_mod.__file__)
        source = source_path.read_text()
        assert 'if __name__ == "__main__"' in source
