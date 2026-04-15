"""Unit tests for core/resolutions.py — file-edit resolution system."""

from __future__ import annotations

import hashlib

import yaml
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from onemancompany.core import resolutions as res


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Redirect RESOLUTIONS_DIR and clear in-memory state for every test."""
    monkeypatch.setattr(res, "RESOLUTIONS_DIR", tmp_path / "resolutions")
    res._task_edits.clear()
    yield


def _make_edit(idx: int = 0, **overrides) -> dict:
    defaults = {
        "edit_id": f"edit_{idx:03d}",
        "file_path": f"/tmp/test/file_{idx}.py",
        "rel_path": f"file_{idx}.py",
        "old_content": f"old content {idx}",
        "new_content": f"new content {idx}",
        "reason": f"reason {idx}",
        "proposed_by": "00005",
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# _compute_md5
# ---------------------------------------------------------------------------

class TestComputeMd5:
    def test_known_hash(self):
        expected = hashlib.md5(b"hello").hexdigest()
        assert res._compute_md5("hello") == expected

    def test_empty_string(self):
        expected = hashlib.md5(b"").hexdigest()
        assert res._compute_md5("") == expected


# ---------------------------------------------------------------------------
# collect_edit
# ---------------------------------------------------------------------------

class TestCollectEdit:
    def test_first_edit(self):
        edit = _make_edit(0)
        res.collect_edit("proj-1", edit)
        assert "proj-1" in res._task_edits
        assert len(res._task_edits["proj-1"]) == 1

    def test_multiple_edits_same_project(self):
        res.collect_edit("proj-1", _make_edit(0))
        res.collect_edit("proj-1", _make_edit(1))
        assert len(res._task_edits["proj-1"]) == 2

    def test_different_projects(self):
        res.collect_edit("proj-1", _make_edit(0))
        res.collect_edit("proj-2", _make_edit(1))
        assert len(res._task_edits["proj-1"]) == 1
        assert len(res._task_edits["proj-2"]) == 1


# ---------------------------------------------------------------------------
# create_resolution
# ---------------------------------------------------------------------------

class TestCreateResolution:
    def test_returns_none_when_no_edits(self):
        result = res.create_resolution("proj-empty", "Build feature")
        assert result is None

    def test_creates_resolution_yaml(self, tmp_path):
        res.collect_edit("proj-1", _make_edit(0))
        result = res.create_resolution("proj-1", "Build feature")

        assert result is not None
        assert result["project_id"] == "proj-1"
        assert result["task"] == "Build feature"
        assert result["status"] == "pending"
        assert len(result["edits"]) == 1

        # YAML file was written
        yaml_files = list((tmp_path / "resolutions").glob("*.yaml"))
        assert len(yaml_files) == 1

    def test_resolution_edits_have_correct_fields(self):
        res.collect_edit("proj-1", _make_edit(0))
        result = res.create_resolution("proj-1", "task")

        edit = result["edits"][0]
        assert edit["edit_id"] == "edit_000"
        assert edit["file_path"] == "/tmp/test/file_0.py"
        assert edit["decision"] is None
        assert edit["decided_at"] is None
        assert edit["executed"] is False
        assert edit["expired"] is False
        assert "original_md5" in edit

    def test_flushes_accumulator(self):
        res.collect_edit("proj-1", _make_edit(0))
        res.create_resolution("proj-1", "task")
        # Second call returns None since edits were flushed
        assert res.create_resolution("proj-1", "task") is None
        assert "proj-1" not in res._task_edits

    def test_multiple_edits(self):
        res.collect_edit("proj-1", _make_edit(0))
        res.collect_edit("proj-1", _make_edit(1))
        res.collect_edit("proj-1", _make_edit(2))
        result = res.create_resolution("proj-1", "task")
        assert len(result["edits"]) == 3

    def test_auto_generated_edit_ids(self):
        # Edit without an edit_id should get auto-generated one
        edit = {"file_path": "/tmp/f.py", "old_content": "a", "new_content": "b"}
        res.collect_edit("proj-1", edit)
        result = res.create_resolution("proj-1", "task")
        assert result["edits"][0]["edit_id"] == "edit_000"


# ---------------------------------------------------------------------------
# load_resolution / _save_resolution
# ---------------------------------------------------------------------------

class TestLoadSaveResolution:
    def test_load_nonexistent(self):
        assert res.load_resolution("nonexistent") is None

    def test_roundtrip(self):
        res.collect_edit("proj-1", _make_edit(0))
        result = res.create_resolution("proj-1", "task")
        loaded = res.load_resolution(result["resolution_id"])
        assert loaded is not None
        assert loaded["resolution_id"] == result["resolution_id"]
        assert len(loaded["edits"]) == 1

    def test_save_resolution_updates_file(self):
        res.collect_edit("proj-1", _make_edit(0))
        result = res.create_resolution("proj-1", "task")
        result["status"] = "decided"
        res._save_resolution(result)
        loaded = res.load_resolution(result["resolution_id"])
        assert loaded["status"] == "decided"


# ---------------------------------------------------------------------------
# list_resolutions
# ---------------------------------------------------------------------------

class TestListResolutions:
    def test_empty(self):
        assert res.list_resolutions() == []

    def test_lists_all(self):
        res.collect_edit("p1", _make_edit(0))
        res.create_resolution("p1", "task 1")
        res.collect_edit("p2", _make_edit(1))
        res.create_resolution("p2", "task 2")
        results = res.list_resolutions()
        assert len(results) == 2

    def test_filter_by_status(self):
        res.collect_edit("p1", _make_edit(0))
        r = res.create_resolution("p1", "task 1")
        r["status"] = "decided"
        res._save_resolution(r)

        res.collect_edit("p2", _make_edit(1))
        res.create_resolution("p2", "task 2")

        pending = res.list_resolutions(status_filter="pending")
        decided = res.list_resolutions(status_filter="decided")
        assert len(pending) == 1
        assert len(decided) == 1

    def test_summary_fields(self):
        res.collect_edit("p1", _make_edit(0))
        res.collect_edit("p1", _make_edit(1))
        res.create_resolution("p1", "task 1")
        results = res.list_resolutions()
        assert len(results) == 1
        r = results[0]
        assert "resolution_id" in r
        assert "project_id" in r
        assert r["edit_count"] == 2


# ---------------------------------------------------------------------------
# list_deferred_edits
# ---------------------------------------------------------------------------

class TestListDeferredEdits:
    def test_empty_when_no_resolutions(self):
        assert res.list_deferred_edits() == []

    def test_returns_deferred_edits(self, tmp_path):
        res.collect_edit("p1", _make_edit(0, file_path=str(tmp_path / "f.py")))
        r = res.create_resolution("p1", "task")
        # Manually set an edit as deferred
        r["edits"][0]["decision"] = "defer"
        r["edits"][0]["original_md5"] = res._compute_md5("old content 0")
        res._save_resolution(r)

        # File doesn't exist — should be marked expired
        deferred = res.list_deferred_edits()
        assert len(deferred) == 1
        assert deferred[0]["expired"] is True

    def test_deferred_not_expired_when_file_unchanged(self, tmp_path):
        file_path = tmp_path / "file.py"
        file_path.write_text("original", encoding="utf-8")
        original_md5 = res._compute_md5("original")

        res.collect_edit("p1", _make_edit(0, file_path=str(file_path), old_content="original"))
        r = res.create_resolution("p1", "task")
        r["edits"][0]["decision"] = "defer"
        r["edits"][0]["original_md5"] = original_md5
        res._save_resolution(r)

        deferred = res.list_deferred_edits()
        assert len(deferred) == 1
        assert deferred[0]["expired"] is False

    def test_deferred_expired_when_file_changed(self, tmp_path):
        file_path = tmp_path / "file.py"
        file_path.write_text("original", encoding="utf-8")
        original_md5 = res._compute_md5("original")

        res.collect_edit("p1", _make_edit(0, file_path=str(file_path), old_content="original"))
        r = res.create_resolution("p1", "task")
        r["edits"][0]["decision"] = "defer"
        r["edits"][0]["original_md5"] = original_md5
        res._save_resolution(r)

        # Now change the file
        file_path.write_text("modified", encoding="utf-8")
        deferred = res.list_deferred_edits()
        assert len(deferred) == 1
        assert deferred[0]["expired"] is True

    def test_executed_deferred_excluded(self):
        res.collect_edit("p1", _make_edit(0))
        r = res.create_resolution("p1", "task")
        r["edits"][0]["decision"] = "defer"
        r["edits"][0]["executed"] = True
        res._save_resolution(r)
        assert res.list_deferred_edits() == []


# ---------------------------------------------------------------------------
# decide_resolution
# ---------------------------------------------------------------------------

class TestDecideResolution:
    def test_resolution_not_found(self):
        result = res.decide_resolution("nonexistent", {"edit_000": "approve"})
        assert result["status"] == "error"

    def test_approve_edit(self):
        import onemancompany.core.file_editor as fe

        res.collect_edit("p1", _make_edit(0))
        r = res.create_resolution("p1", "task")
        rid = r["resolution_id"]

        mock_execute = MagicMock(return_value={"status": "applied"})
        original_pending = fe.pending_file_edits
        fe.pending_file_edits = {}
        try:
            with patch.object(fe, "execute_edit", mock_execute):
                result = res.decide_resolution(rid, {"edit_000": "approve"})
        finally:
            fe.pending_file_edits = original_pending

        assert result["status"] == "ok"
        assert len(result["results"]) == 1
        assert result["results"][0]["decision"] == "approve"

    def test_reject_edit(self):
        res.collect_edit("p1", _make_edit(0))
        r = res.create_resolution("p1", "task")
        rid = r["resolution_id"]

        result = res.decide_resolution(rid, {"edit_000": "reject"})
        assert result["status"] == "ok"
        assert result["results"][0]["decision"] == "reject"
        assert result["results"][0]["status"] == "rejected"

        # Check persisted
        loaded = res.load_resolution(rid)
        assert loaded["edits"][0]["decision"] == "reject"
        assert loaded["edits"][0]["executed"] is False

    def test_defer_edit(self, tmp_path):
        file_path = tmp_path / "f.py"
        file_path.write_text("content", encoding="utf-8")

        res.collect_edit("p1", _make_edit(0, file_path=str(file_path)))
        r = res.create_resolution("p1", "task")
        rid = r["resolution_id"]

        result = res.decide_resolution(rid, {"edit_000": "defer"})
        assert result["status"] == "ok"
        assert result["results"][0]["decision"] == "defer"

        loaded = res.load_resolution(rid)
        # Status remains pending when there are deferred edits
        assert loaded["status"] == "pending"
        assert loaded["edits"][0]["original_md5"] == res._compute_md5("content")

    def test_all_decided_sets_status_decided(self):
        res.collect_edit("p1", _make_edit(0))
        r = res.create_resolution("p1", "task")
        rid = r["resolution_id"]

        res.decide_resolution(rid, {"edit_000": "reject"})
        loaded = res.load_resolution(rid)
        assert loaded["status"] == "decided"

    def test_skip_unknown_edit_ids(self):
        res.collect_edit("p1", _make_edit(0))
        r = res.create_resolution("p1", "task")
        rid = r["resolution_id"]

        result = res.decide_resolution(rid, {"nonexistent_edit": "approve"})
        assert result["status"] == "ok"
        assert len(result["results"]) == 0


# ---------------------------------------------------------------------------
# execute_deferred_edit
# ---------------------------------------------------------------------------

class TestExecuteDeferredEdit:
    def test_resolution_not_found(self):
        result = res.execute_deferred_edit("nonexistent", "edit_000")
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_edit_not_found(self):
        res.collect_edit("p1", _make_edit(0))
        r = res.create_resolution("p1", "task")
        result = res.execute_deferred_edit(r["resolution_id"], "nonexistent")
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_edit_not_deferred(self):
        res.collect_edit("p1", _make_edit(0))
        r = res.create_resolution("p1", "task")
        rid = r["resolution_id"]
        # Decision is None (not deferred)
        result = res.execute_deferred_edit(rid, "edit_000")
        assert result["status"] == "error"
        assert "not deferred" in result["message"]

    def test_edit_already_executed(self):
        res.collect_edit("p1", _make_edit(0))
        r = res.create_resolution("p1", "task")
        r["edits"][0]["decision"] = "defer"
        r["edits"][0]["executed"] = True
        res._save_resolution(r)
        result = res.execute_deferred_edit(r["resolution_id"], "edit_000")
        assert result["status"] == "error"
        assert "already executed" in result["message"]

    def test_expired_file_changed(self, tmp_path):
        file_path = tmp_path / "f.py"
        file_path.write_text("original", encoding="utf-8")

        res.collect_edit("p1", _make_edit(0, file_path=str(file_path), old_content="original"))
        r = res.create_resolution("p1", "task")
        r["edits"][0]["decision"] = "defer"
        r["edits"][0]["original_md5"] = res._compute_md5("original")
        res._save_resolution(r)

        # Change the file to expire the edit
        file_path.write_text("modified", encoding="utf-8")
        result = res.execute_deferred_edit(r["resolution_id"], "edit_000")
        assert result["status"] == "error"
        assert "expired" in result["message"]

    def test_expired_file_deleted(self, tmp_path):
        file_path = tmp_path / "f.py"
        # Don't create the file — it "doesn't exist"

        res.collect_edit("p1", _make_edit(0, file_path=str(file_path), old_content="something"))
        r = res.create_resolution("p1", "task")
        r["edits"][0]["decision"] = "defer"
        r["edits"][0]["original_md5"] = res._compute_md5("something")
        res._save_resolution(r)

        result = res.execute_deferred_edit(r["resolution_id"], "edit_000")
        assert result["status"] == "error"
        assert "expired" in result["message"]

    def test_successful_execution(self, tmp_path):
        import onemancompany.core.file_editor as fe

        file_path = tmp_path / "f.py"
        file_path.write_text("original", encoding="utf-8")

        res.collect_edit("p1", _make_edit(0, file_path=str(file_path), old_content="original"))
        r = res.create_resolution("p1", "task")
        r["edits"][0]["decision"] = "defer"
        r["edits"][0]["original_md5"] = res._compute_md5("original")
        res._save_resolution(r)

        mock_execute = MagicMock(return_value={"status": "applied"})
        original_pending = fe.pending_file_edits
        fe.pending_file_edits = {}
        try:
            with patch.object(fe, "execute_edit", mock_execute):
                result = res.execute_deferred_edit(r["resolution_id"], "edit_000")
        finally:
            fe.pending_file_edits = original_pending

        # {"status": "ok", **exec_result} — exec_result overrides with "applied"
        assert result["status"] == "applied"
        loaded = res.load_resolution(r["resolution_id"])
        assert loaded["edits"][0]["executed"] is True
        assert loaded["edits"][0]["decision"] == "approve"

    def test_new_file_deferred_can_be_executed(self, tmp_path):
        """New file (old_content empty, file doesn't exist) can still be executed."""
        import onemancompany.core.file_editor as fe

        file_path = tmp_path / "new.py"
        # File doesn't exist — this is a new file creation

        res.collect_edit("p1", _make_edit(0, file_path=str(file_path), old_content=""))
        r = res.create_resolution("p1", "task")
        r["edits"][0]["decision"] = "defer"
        r["edits"][0]["original_md5"] = res._compute_md5("")
        res._save_resolution(r)

        mock_execute = MagicMock(return_value={"status": "applied"})
        original_pending = fe.pending_file_edits
        fe.pending_file_edits = {}
        try:
            with patch.object(fe, "execute_edit", mock_execute):
                result = res.execute_deferred_edit(r["resolution_id"], "edit_000")
        finally:
            fe.pending_file_edits = original_pending

        assert result["status"] == "applied"


# ---------------------------------------------------------------------------
# current_project_id context var
# ---------------------------------------------------------------------------

class TestCurrentProjectId:
    def test_default_value(self):
        assert res.current_project_id.get() == ""

    def test_set_and_get(self):
        token = res.current_project_id.set("proj-123")
        assert res.current_project_id.get() == "proj-123"
        res.current_project_id.reset(token)
        assert res.current_project_id.get() == ""


# ---------------------------------------------------------------------------
# Edge cases for list_resolutions
# ---------------------------------------------------------------------------

class TestListResolutionsEdgeCases:
    def test_skips_non_yaml_files(self, tmp_path):
        """Line 114: non-.yaml files skipped."""
        res_dir = tmp_path / "resolutions"
        res_dir.mkdir(parents=True)
        (res_dir / "readme.txt").write_text("not a resolution")
        results = res.list_resolutions()
        assert results == []

    def test_skips_empty_yaml(self, tmp_path):
        """Line 118: yaml with None/empty content skipped."""
        res_dir = tmp_path / "resolutions"
        res_dir.mkdir(parents=True)
        (res_dir / "empty.yaml").write_text("")
        results = res.list_resolutions()
        assert results == []

    def test_filter_no_match(self, tmp_path):
        """Lines 143, 147: status_filter with no matching results."""
        res.collect_edit("p1", _make_edit(0))
        res.create_resolution("p1", "task")
        results = res.list_resolutions(status_filter="decided")
        assert results == []


# ---------------------------------------------------------------------------
# Edge cases for list_deferred_edits
# ---------------------------------------------------------------------------

class TestListDeferredEditsEdgeCases:
    def test_skips_non_yaml_files(self, tmp_path):
        """Line 143: non-.yaml files skipped in deferred scan."""
        res_dir = tmp_path / "resolutions"
        res_dir.mkdir(parents=True)
        (res_dir / "notes.txt").write_text("not yaml")
        results = res.list_deferred_edits()
        assert results == []

    def test_skips_empty_yaml_in_deferred(self, tmp_path):
        """Line 147: empty yaml skipped."""
        res_dir = tmp_path / "resolutions"
        res_dir.mkdir(parents=True)
        (res_dir / "empty.yaml").write_text("")
        results = res.list_deferred_edits()
        assert results == []

    def test_skips_non_deferred_edits(self, tmp_path):
        """Line 150: only deferred edits returned."""
        res.collect_edit("p1", _make_edit(0))
        r = res.create_resolution("p1", "task")
        r["edits"][0]["decision"] = "reject"
        res._save_resolution(r)
        results = res.list_deferred_edits()
        assert results == []


class TestResolutionsSnapshot:
    def test_save_empty_returns_empty_dict(self):
        """Lines 319-321: snapshot save returns {} when _task_edits is empty."""
        from onemancompany.core.resolutions import _ResolutionsSnapshot, _task_edits
        _task_edits.clear()
        result = _ResolutionsSnapshot.save()
        assert result == {}

    def test_save_with_data_returns_task_edits(self):
        """Line 321: snapshot save returns task_edits when non-empty."""
        from onemancompany.core.resolutions import _ResolutionsSnapshot, _task_edits
        _task_edits.clear()
        _task_edits["t1"] = {"something": True}
        result = _ResolutionsSnapshot.save()
        assert result == {"task_edits": {"t1": {"something": True}}}
        _task_edits.clear()

    def test_restore_merges_task_edits(self):
        """Lines 325-327: snapshot restore merges saved data."""
        from onemancompany.core.resolutions import _ResolutionsSnapshot, _task_edits
        _task_edits.clear()
        _ResolutionsSnapshot.restore({"task_edits": {"t2": {"key": "val"}}})
        assert _task_edits["t2"] == {"key": "val"}
        _task_edits.clear()

    def test_restore_empty_data(self):
        """Lines 325-327: restore with empty data is a no-op."""
        from onemancompany.core.resolutions import _ResolutionsSnapshot, _task_edits
        _task_edits.clear()
        _ResolutionsSnapshot.restore({})
        assert len(_task_edits) == 0
