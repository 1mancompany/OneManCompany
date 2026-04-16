"""Coverage tests for core/vessel.py — additional missing lines."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# _parse_holding_metadata (lines 297, 381-384, 421-422)
# ---------------------------------------------------------------------------

class TestParseHoldingMetadata:
    def test_none_result(self):
        from onemancompany.core.vessel import _parse_holding_metadata
        assert _parse_holding_metadata(None) is None

    def test_no_prefix(self):
        from onemancompany.core.vessel import _parse_holding_metadata
        assert _parse_holding_metadata("normal result") is None

    def test_empty_payload(self):
        from onemancompany.core.vessel import _parse_holding_metadata
        result = _parse_holding_metadata("__HOLDING:")
        assert result == {}

    def test_with_metadata(self):
        from onemancompany.core.vessel import _parse_holding_metadata
        result = _parse_holding_metadata("__HOLDING:wait_for=child,no_watchdog=true")
        assert result is not None
        assert "wait_for" in result or "no_watchdog" in result


# ---------------------------------------------------------------------------
# _save_task_history (lines 421-422)
# ---------------------------------------------------------------------------

class TestSaveTaskHistory:
    def test_save_task_history(self, tmp_path, monkeypatch):
        import onemancompany.core.vessel as vessel_mod
        monkeypatch.setattr(vessel_mod, "EMPLOYEES_DIR", tmp_path)
        vessel_mod._save_task_history("00010", [{"id": "t1"}], "summary")
        # Verify file was created
        history_path = tmp_path / "00010" / "task_history.json"
        assert history_path.exists()

    def test_save_task_history_error(self, tmp_path, monkeypatch):
        import onemancompany.core.vessel as vessel_mod
        # Point to a path where parent can't be created
        monkeypatch.setattr(vessel_mod, "EMPLOYEES_DIR", Path("/nonexistent/path"))
        # Should not raise
        vessel_mod._save_task_history("00010", [], "summary")


# ---------------------------------------------------------------------------
# stop_cron wrapper (line 611)
# ---------------------------------------------------------------------------

class TestStopCronWrapper:
    def test_stop_cron_delegates(self):
        from onemancompany.core.vessel import stop_cron
        with patch("onemancompany.core.automation.stop_cron", return_value={"status": "ok"}) as mock:
            result = stop_cron("00010", "test_cron")
        assert result["status"] == "ok"
        mock.assert_called_once_with("00010", "test_cron")


# ---------------------------------------------------------------------------
# ScheduleEntry (lines 1067)
# ---------------------------------------------------------------------------

class TestScheduleEntry:
    def test_schedule_entry_creation(self):
        from onemancompany.core.vessel import ScheduleEntry
        entry = ScheduleEntry(node_id="n1", tree_path="/tmp/tree.yaml")
        assert entry.node_id == "n1"
        assert entry.tree_path == "/tmp/tree.yaml"


# ---------------------------------------------------------------------------
# ScheduleEntry basic usage
# ---------------------------------------------------------------------------

class TestScheduleEntryUsage:
    def test_schedule_entry_in_list(self):
        from onemancompany.core.vessel import ScheduleEntry
        entries = [
            ScheduleEntry("n1", "/tmp/t.yaml"),
            ScheduleEntry("n2", "/tmp/t.yaml"),
        ]
        assert len(entries) == 2
        assert entries[0].node_id == "n1"


# ---------------------------------------------------------------------------
# _save_project_tree / _trigger_dep_resolution (lines 381-384)
# ---------------------------------------------------------------------------

class TestSaveProjectTree:
    def test_save_project_tree(self, tmp_path):
        from onemancompany.core.vessel import _save_project_tree
        from onemancompany.core.task_tree import TaskTree
        tree = TaskTree(project_id="proj1")
        _save_project_tree(str(tmp_path), tree)
        assert (tmp_path / "task_tree.yaml").exists()
