"""Coverage tests for core/automation.py — missing lines."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_employee(tmp_path, monkeypatch, employee_id: str = "00010"):
    import onemancompany.core.automation as auto_mod
    import onemancompany.core.config as config_mod
    monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
    monkeypatch.setattr(auto_mod, "EMPLOYEES_DIR", tmp_path / "employees")
    emp_dir = tmp_path / "employees" / employee_id
    emp_dir.mkdir(parents=True)
    return emp_dir


# ---------------------------------------------------------------------------
# _load_automations / _save_automations (lines 42, 48-49)
# ---------------------------------------------------------------------------

class TestLoadSaveAutomations:
    def test_load_missing_returns_default(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import _load_automations
        result = _load_automations("00010")
        assert result == {"crons": [], "webhooks": []}

    def test_load_corrupt_returns_default(self, tmp_path, monkeypatch):
        emp_dir = _setup_employee(tmp_path, monkeypatch)
        (emp_dir / "automations.yaml").write_text(":::bad yaml")
        from onemancompany.core.automation import _load_automations
        result = _load_automations("00010")
        assert result == {"crons": [], "webhooks": []}

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import _save_automations, _load_automations
        data = {"crons": [{"name": "test_cron"}], "webhooks": []}
        _save_automations("00010", data)
        loaded = _load_automations("00010")
        assert loaded["crons"][0]["name"] == "test_cron"


# ---------------------------------------------------------------------------
# _broadcast_cron_status (lines 82-89)
# ---------------------------------------------------------------------------

class TestBroadcastCronStatus:
    def test_broadcast_no_event_loop(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import _broadcast_cron_status
        # Should not raise when no event loop is running
        _broadcast_cron_status("00010", "test_cron", True)


# ---------------------------------------------------------------------------
# _cron_loop (lines 101-118)
# ---------------------------------------------------------------------------

class TestCronLoop:
    @pytest.mark.asyncio
    async def test_cron_loop_dispatches_and_cancels(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import _cron_loop

        call_count = 0

        def mock_dispatch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return "node_001"

        with patch("onemancompany.core.automation._dispatch_cron_task", side_effect=mock_dispatch), \
             patch("onemancompany.core.automation._record_dispatched_task"), \
             patch("asyncio.sleep", new_callable=AsyncMock, side_effect=[None, asyncio.CancelledError]):
            with pytest.raises(asyncio.CancelledError):
                await _cron_loop("00010", "test", 60, "do stuff")
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_cron_loop_handles_dispatch_error(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import _cron_loop

        with patch("onemancompany.core.automation._dispatch_cron_task",
                   side_effect=[RuntimeError("boom"), None]), \
             patch("onemancompany.core.automation._record_dispatched_task"), \
             patch("asyncio.sleep", new_callable=AsyncMock,
                   side_effect=[None, asyncio.CancelledError]):
            with pytest.raises(asyncio.CancelledError):
                await _cron_loop("00010", "test", 60, "do stuff")


# ---------------------------------------------------------------------------
# _dispatch_cron_task — tree fallback (lines 130-141)
# ---------------------------------------------------------------------------

class TestDispatchCronTask:
    def test_dispatch_with_tree_path_failure_falls_back(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import _dispatch_cron_task

        with patch("onemancompany.core.automation._add_to_project_tree",
                   side_effect=FileNotFoundError("no tree")), \
             patch("onemancompany.api.routes._push_adhoc_task",
                   return_value=("node_1", "/tmp/tree.yaml")) as mock_adhoc:
            node_id = _dispatch_cron_task("00010", "cron1", "task desc",
                                         tree_path="/nonexistent/tree.yaml")
        assert node_id == "node_1"
        mock_adhoc.assert_called_once()

    def test_dispatch_without_tree_path(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import _dispatch_cron_task

        with patch("onemancompany.api.routes._push_adhoc_task",
                   return_value=("node_2", "/tmp/tree.yaml")):
            node_id = _dispatch_cron_task("00010", "cron1", "task desc")
        assert node_id == "node_2"


# ---------------------------------------------------------------------------
# _add_to_project_tree (lines 155, 161, 168-186)
# ---------------------------------------------------------------------------

class TestAddToProjectTree:
    def test_tree_not_found(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import _add_to_project_tree

        with pytest.raises(FileNotFoundError):
            _add_to_project_tree("00010", "desc", "/nonexistent/tree.yaml", "proj1")


# ---------------------------------------------------------------------------
# _record_dispatched_task (lines 191-200)
# ---------------------------------------------------------------------------

class TestRecordDispatchedTask:
    def test_records_task_id(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import (
            _record_dispatched_task, _save_automations, _load_automations,
        )
        _save_automations("00010", {
            "crons": [{"name": "cron1", "dispatched_task_ids": []}],
            "webhooks": [],
        })
        _record_dispatched_task("00010", "cron1", "node_001")
        data = _load_automations("00010")
        assert "node_001" in data["crons"][0]["dispatched_task_ids"]

    def test_records_task_id_caps_at_100(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import (
            _record_dispatched_task, _save_automations, _load_automations,
        )
        _save_automations("00010", {
            "crons": [{"name": "cron1", "dispatched_task_ids": [f"n{i}" for i in range(100)]}],
            "webhooks": [],
        })
        _record_dispatched_task("00010", "cron1", "n100")
        data = _load_automations("00010")
        assert len(data["crons"][0]["dispatched_task_ids"]) == 100
        assert "n100" in data["crons"][0]["dispatched_task_ids"]


# ---------------------------------------------------------------------------
# start_cron (lines 216, 223, 260-275)
# ---------------------------------------------------------------------------

class TestStartCron:
    def test_start_cron_invalid_interval(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        from onemancompany.core.automation import start_cron
        result = start_cron("00010", "cron1", "1s", "task desc")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_start_cron_replaces_existing(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        # Place a "running" task in _cron_tasks
        mock_task = MagicMock()
        mock_task.done.return_value = False
        auto_mod._cron_tasks["00010:cron1"] = mock_task

        result = auto_mod.start_cron("00010", "cron1", "1m", "new desc")
        assert result["status"] == "ok"
        mock_task.cancel.assert_called_once()
        # Cleanup
        task = auto_mod._cron_tasks.pop("00010:cron1", None)
        if task and hasattr(task, 'cancel'):
            task.cancel()


# ---------------------------------------------------------------------------
# stop_cron (lines 279-282, 320-349)
# ---------------------------------------------------------------------------

class TestStopCron:
    def test_stop_cron_basic(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        auto_mod._save_automations("00010", {
            "crons": [{"name": "cron1", "dispatched_task_ids": []}],
            "webhooks": [],
        })

        mock_task = MagicMock()
        mock_task.done.return_value = False
        auto_mod._cron_tasks["00010:cron1"] = mock_task

        with patch.object(auto_mod, "_cancel_cron_tasks", return_value=[]):
            result = auto_mod.stop_cron("00010", "cron1")
        assert result["status"] == "ok"
        mock_task.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# list_crons — orphan detection (lines 320-349)
# ---------------------------------------------------------------------------

class TestListCrons:
    def test_list_crons_includes_orphan(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        auto_mod._save_automations("00010", {"crons": [], "webhooks": []})

        # Orphan in-memory task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        auto_mod._cron_tasks["00010:orphan_cron"] = mock_task

        result = auto_mod.list_crons("00010")
        names = [c["name"] for c in result]
        assert "orphan_cron" in names
        # Cleanup
        auto_mod._cron_tasks.pop("00010:orphan_cron", None)


# ---------------------------------------------------------------------------
# stop_all_crons_for_employee (lines 365, 393-418)
# ---------------------------------------------------------------------------

class TestStopAllCronsForEmployee:
    def test_stop_all_basic(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        auto_mod._save_automations("00010", {
            "crons": [{"name": "c1"}, {"name": "c2"}],
            "webhooks": [],
        })

        t1 = MagicMock()
        t1.done.return_value = False
        t2 = MagicMock()
        t2.done.return_value = False
        auto_mod._cron_tasks["00010:c1"] = t1
        auto_mod._cron_tasks["00010:c2"] = t2

        with patch.object(auto_mod, "_cancel_cron_tasks", return_value=[]):
            result = auto_mod.stop_all_crons_for_employee("00010")
        assert result["count"] == 2
        t1.cancel.assert_called_once()
        t2.cancel.assert_called_once()


# ---------------------------------------------------------------------------
# restore_all_crons (lines 393-418)
# ---------------------------------------------------------------------------

class TestRestoreAllCrons:
    @pytest.mark.asyncio
    async def test_restore_all_crons(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        auto_mod._save_automations("00010", {
            "crons": [{
                "name": "restore_test",
                "interval": "1m",
                "task_description": "periodic task",
            }],
            "webhooks": [],
        })

        count = auto_mod.restore_all_crons()
        assert count == 1
        assert "00010:restore_test" in auto_mod._cron_tasks
        # Cleanup
        task = auto_mod._cron_tasks.pop("00010:restore_test", None)
        if task:
            task.cancel()

    def test_restore_all_crons_no_employees_dir(self, tmp_path, monkeypatch):
        import onemancompany.core.automation as auto_mod
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(auto_mod, "EMPLOYEES_DIR", tmp_path / "nonexistent")
        count = auto_mod.restore_all_crons()
        assert count == 0


# ---------------------------------------------------------------------------
# register_webhook / unregister_webhook / handle_webhook (lines 440-494)
# ---------------------------------------------------------------------------

class TestWebhooks:
    def test_register_webhook(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        result = auto_mod.register_webhook("00010", "gh_push", "PR from {payload}")
        assert result["status"] == "ok"
        assert result["url"] == "/api/webhook/00010/gh_push"
        # Cleanup
        auto_mod._webhook_registry.pop("00010:gh_push", None)

    def test_unregister_webhook(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        auto_mod.register_webhook("00010", "gh_push")
        result = auto_mod.unregister_webhook("00010", "gh_push")
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_handle_webhook_not_registered(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        result = await auto_mod.handle_webhook("00010", "nonexistent", {"data": 1})
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_handle_webhook_dispatches(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        auto_mod._webhook_registry["00010:wh1"] = {
            "employee_id": "00010",
            "hook_name": "wh1",
            "task_template": "Webhook {hook_name}: {payload}",
        }

        with patch("onemancompany.api.routes._push_adhoc_task") as mock_push:
            result = await auto_mod.handle_webhook("00010", "wh1", {"key": "val"})
        assert result["status"] == "ok"
        mock_push.assert_called_once()
        # Cleanup
        auto_mod._webhook_registry.pop("00010:wh1", None)

    @pytest.mark.asyncio
    async def test_handle_webhook_dispatch_failure(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        auto_mod._webhook_registry["00010:wh1"] = {
            "employee_id": "00010",
            "hook_name": "wh1",
            "task_template": "test: {payload}",
        }

        with patch("onemancompany.api.routes._push_adhoc_task",
                   side_effect=RuntimeError("boom")):
            result = await auto_mod.handle_webhook("00010", "wh1", {})
        assert result["status"] == "error"
        auto_mod._webhook_registry.pop("00010:wh1", None)


# ---------------------------------------------------------------------------
# restore_all_webhooks (lines 497-518)
# ---------------------------------------------------------------------------

class TestRestoreAllWebhooks:
    def test_restore_all_webhooks(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        auto_mod._save_automations("00010", {
            "crons": [],
            "webhooks": [{"name": "wh1", "task_template": "test: {payload}"}],
        })

        count = auto_mod.restore_all_webhooks()
        assert count == 1
        assert "00010:wh1" in auto_mod._webhook_registry
        auto_mod._webhook_registry.pop("00010:wh1", None)

    def test_restore_all_webhooks_no_dir(self, tmp_path, monkeypatch):
        import onemancompany.core.automation as auto_mod
        monkeypatch.setattr(auto_mod, "EMPLOYEES_DIR", tmp_path / "nonexistent")
        count = auto_mod.restore_all_webhooks()
        assert count == 0


# ---------------------------------------------------------------------------
# list_webhooks (lines 523-524)
# ---------------------------------------------------------------------------

class TestListWebhooks:
    def test_list_webhooks(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        auto_mod._save_automations("00010", {
            "crons": [],
            "webhooks": [{"name": "wh1", "task_template": "test"}],
        })
        result = auto_mod.list_webhooks("00010")
        assert len(result) == 1
        assert result[0]["name"] == "wh1"


# ---------------------------------------------------------------------------
# list_all_crons (lines 545, 549)
# ---------------------------------------------------------------------------

class TestListAllCrons:
    def test_list_all_crons(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        auto_mod._save_automations("00010", {
            "crons": [{"name": "c1", "interval": "1m", "task_description": "test"}],
            "webhooks": [],
        })

        with patch("onemancompany.core.system_cron.system_cron_manager") as mock_sys:
            mock_sys.get_all.return_value = []
            result = auto_mod.list_all_crons()
        assert any(c["name"] == "c1" for c in result)


# ---------------------------------------------------------------------------
# stop_all_automations (lines 572-579)
# ---------------------------------------------------------------------------

class TestStopAllAutomations:
    @pytest.mark.asyncio
    async def test_stop_all_automations(self, tmp_path, monkeypatch):
        _setup_employee(tmp_path, monkeypatch)
        import onemancompany.core.automation as auto_mod

        mock_task = MagicMock()
        mock_task.done.return_value = False
        auto_mod._cron_tasks["00010:c1"] = mock_task
        auto_mod._webhook_registry["00010:wh1"] = {"name": "wh1"}

        count = await auto_mod.stop_all_automations()
        assert count == 1
        assert len(auto_mod._cron_tasks) == 0
        assert len(auto_mod._webhook_registry) == 0
