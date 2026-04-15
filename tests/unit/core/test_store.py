"""Tests for core/store.py — unified read/write layer.

NOTE: tests/unit/conftest.py has autouse fixtures that patch many store functions
(save_employee, save_ex_employee, save_task_index, append_task_index_entry, etc.)
to bridge to CompanyState or no-op. We capture the ORIGINAL functions before the
conftest runs by importing at module level, and call them directly.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Import originals BEFORE conftest can patch them.
# We'll use these in tests that need the real disk-writing behavior.
import onemancompany.core.store as _store_mod

_ORIG_save_employee = _store_mod.save_employee
_ORIG_save_employee_runtime = _store_mod.save_employee_runtime
_ORIG_save_ex_employee = _store_mod.save_ex_employee
_ORIG_save_guidance = _store_mod.save_guidance
_ORIG_save_work_principles = _store_mod.save_work_principles
_ORIG_save_task_index = _store_mod.save_task_index
_ORIG_append_task_index_entry = _store_mod.append_task_index_entry
_ORIG_load_task_index = _store_mod.load_task_index
_ORIG_load_employee = _store_mod.load_employee
_ORIG_load_all_employees = _store_mod.load_all_employees
_ORIG_load_ex_employees = _store_mod.load_ex_employees
_ORIG_load_employee_guidance = _store_mod.load_employee_guidance
_ORIG_load_employee_work_principles = _store_mod.load_employee_work_principles
_ORIG_save_room = _store_mod.save_room
_ORIG_append_room_chat = _store_mod.append_room_chat
_ORIG_clear_room_chat = _store_mod.clear_room_chat
_ORIG_append_activity = _store_mod.append_activity
_ORIG_append_activity_sync = _store_mod.append_activity_sync
_ORIG_load_activity_log = _store_mod.load_activity_log
_ORIG_load_culture = _store_mod.load_culture
_ORIG_load_direction = _store_mod.load_direction
_ORIG_append_oneonone = _store_mod.append_oneonone

from onemancompany.core.store import (
    _atomic_write_text,
    _read_yaml,
    _read_yaml_list,
    _write_yaml,
    cache_clear_all,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear read cache and dirty set before each test."""
    cache_clear_all()
    _store_mod._dirty.clear()
    _store_mod._file_locks.clear()
    yield
    cache_clear_all()
    _store_mod._dirty.clear()


# ---------------------------------------------------------------------------
# _read_yaml
# ---------------------------------------------------------------------------

class TestReadYaml:
    def test_returns_dict(self, tmp_path):
        p = tmp_path / "test.yaml"
        p.write_text("name: Alice\nage: 30\n")
        assert _read_yaml(p) == {"name": "Alice", "age": 30}

    def test_missing_returns_empty(self, tmp_path):
        assert _read_yaml(tmp_path / "missing.yaml") == {}

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        assert _read_yaml(p) == {}

    def test_exception_returns_empty(self, tmp_path):
        """_read_yaml logs and returns {} on parse errors."""
        p = tmp_path / "bad.yaml"
        p.write_text("!!invalid\x00\x01")
        result = _read_yaml(p)
        assert result == {}


# ---------------------------------------------------------------------------
# _read_yaml_list
# ---------------------------------------------------------------------------

class TestReadYamlList:
    def test_returns_list(self, tmp_path):
        p = tmp_path / "items.yaml"
        p.write_text("- name: A\n- name: B\n")
        result = _read_yaml_list(p)
        assert len(result) == 2

    def test_missing_returns_empty(self, tmp_path):
        assert _read_yaml_list(tmp_path / "missing.yaml") == []

    def test_non_list_returns_empty(self, tmp_path):
        p = tmp_path / "notlist.yaml"
        p.write_text("key: value\n")
        assert _read_yaml_list(p) == []

    def test_exception_returns_empty(self, tmp_path):
        """_read_yaml_list logs and returns [] on parse errors."""
        p = tmp_path / "bad.yaml"
        p.write_text("!!invalid\x00\x01")
        assert _read_yaml_list(p) == []


# ---------------------------------------------------------------------------
# _write_yaml / _atomic_write_text
# ---------------------------------------------------------------------------

class TestWriteYaml:
    def test_creates_file(self, tmp_path):
        p = tmp_path / "out.yaml"
        _write_yaml(p, {"name": "Bob", "skills": ["python"]})
        loaded = yaml.safe_load(p.read_text())
        assert loaded["name"] == "Bob"
        assert loaded["skills"] == ["python"]

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "sub" / "deep" / "out.yaml"
        _write_yaml(p, {"key": "val"})
        assert p.exists()

    def test_error_propagates(self, tmp_path, monkeypatch):
        """_write_yaml re-raises on error."""
        p = tmp_path / "fail.yaml"

        def bad_dump(*a, **kw):
            raise TypeError("bad dump")

        monkeypatch.setattr(yaml, "dump", bad_dump)
        with pytest.raises(TypeError, match="bad dump"):
            _write_yaml(p, {"key": "val"})


class TestAtomicWriteText:
    def test_uses_os_replace(self):
        import inspect
        source = inspect.getsource(_atomic_write_text)
        assert "os.replace" in source

    def test_writes_file(self, tmp_path):
        p = tmp_path / "atomic.txt"
        _atomic_write_text(p, "hello world")
        assert p.read_text() == "hello world"

    def test_preserves_original_on_replace_error(self, tmp_path, monkeypatch):
        """Original file remains intact if os.replace fails, temp is cleaned."""
        p = tmp_path / "keep.txt"
        _atomic_write_text(p, "original")

        def failing_replace(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", failing_replace)
        with pytest.raises(OSError, match="disk full"):
            _atomic_write_text(p, "corrupted")

        assert p.read_text() == "original"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_temp_unlink_failure_still_raises(self, tmp_path, monkeypatch):
        """If temp cleanup also fails, the original error still propagates."""
        p = tmp_path / "tricky.txt"

        def failing_replace(src, dst):
            raise OSError("replace fail")

        def failing_unlink(path):
            raise OSError("unlink fail")

        monkeypatch.setattr(os, "replace", failing_replace)
        monkeypatch.setattr(os, "unlink", failing_unlink)

        with pytest.raises(OSError, match="replace fail"):
            _atomic_write_text(p, "content")


# ---------------------------------------------------------------------------
# _enum_representer
# ---------------------------------------------------------------------------

class TestEnumRepresenter:
    def test_enum_serialized_as_string(self, tmp_path):
        """Enum values should be serialized as plain strings."""
        from onemancompany.core.config import DirtyCategory
        p = tmp_path / "enum_test.yaml"
        _write_yaml(p, {"cat": DirtyCategory.EMPLOYEES})
        text = p.read_text()
        assert "!!python" not in text
        assert "employees" in text


# ---------------------------------------------------------------------------
# Dirty tracking
# ---------------------------------------------------------------------------

class TestDirtyTracking:
    def test_mark_dirty_and_flush(self):
        from onemancompany.core.store import mark_dirty, flush_dirty
        mark_dirty("employees", "rooms")
        changed = flush_dirty()
        assert set(changed) == {"employees", "rooms"}
        assert flush_dirty() == []

    def test_flush_dirty_returns_empty_initially(self):
        from onemancompany.core.store import flush_dirty
        assert flush_dirty() == []


# ---------------------------------------------------------------------------
# mark_dirty_for_path
# ---------------------------------------------------------------------------

class TestMarkDirtyForPath:
    def test_detects_employee_path(self, monkeypatch):
        from onemancompany.core.config import DirtyCategory
        monkeypatch.setattr(_store_mod, "_path_dirty_registry", None)
        _store_mod.mark_dirty_for_path(_store_mod.EMPLOYEES_DIR / "00100" / "profile.yaml")
        assert DirtyCategory.EMPLOYEES in _store_mod._dirty

    def test_unmatched_path_does_not_mark(self, monkeypatch):
        monkeypatch.setattr(_store_mod, "_path_dirty_registry", None)
        _store_mod._dirty.clear()
        _store_mod.mark_dirty_for_path(Path("/tmp/random/file.txt"))
        assert len(_store_mod._dirty) == 0

    def test_exception_in_resolve_does_not_crash(self, monkeypatch):
        monkeypatch.setattr(_store_mod, "_path_dirty_registry", None)
        bad_path = MagicMock(spec=Path)
        bad_path.resolve.side_effect = OSError("broken")
        _store_mod.mark_dirty_for_path(bad_path)


# ---------------------------------------------------------------------------
# _build_path_dirty_registry
# ---------------------------------------------------------------------------

class TestBuildPathDirtyRegistry:
    def test_returns_sorted_by_depth(self):
        from onemancompany.core.store import _build_path_dirty_registry
        registry = _build_path_dirty_registry()
        assert len(registry) > 0
        depths = [len(p.parts) for p, _ in registry]
        assert depths == sorted(depths, reverse=True)


# ---------------------------------------------------------------------------
# Per-file locks
# ---------------------------------------------------------------------------

class TestGetLock:
    def test_returns_same_lock_for_same_path(self):
        from onemancompany.core.store import _get_lock
        lock1 = _get_lock("/some/path")
        lock2 = _get_lock("/some/path")
        assert lock1 is lock2

    def test_different_path_different_lock(self):
        from onemancompany.core.store import _get_lock
        lock1 = _get_lock("/path/a")
        lock2 = _get_lock("/path/b")
        assert lock1 is not lock2


# ---------------------------------------------------------------------------
# Employee reads — call originals directly to bypass conftest bridge
# ---------------------------------------------------------------------------

class TestLoadEmployee:
    def test_loads_profile_with_work_principles_and_guidance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00100"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text("name: TestBot\nrole: Engineer\n")
        (emp_dir / "work_principles.md").write_text("Be excellent")
        (emp_dir / "guidance.yaml").write_text("notes:\n  - do stuff\n")

        result = _ORIG_load_employee("00100")
        assert result["name"] == "TestBot"
        assert result["work_principles"] == "Be excellent"
        assert result["guidance_notes"] == ["do stuff"]

    def test_missing_employee_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        result = _ORIG_load_employee("99999")
        assert not result


class TestLoadAllEmployees:
    def test_reads_all_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        for eid in ["00002", "00100"]:
            d = tmp_path / eid
            d.mkdir()
            (d / "profile.yaml").write_text(f"name: Emp{eid}\n")
        result = _ORIG_load_all_employees()
        assert len(result) == 2

    def test_skips_non_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "README.md").write_text("ignore me")
        d = tmp_path / "00010"
        d.mkdir()
        (d / "profile.yaml").write_text("name: Only\n")
        result = _ORIG_load_all_employees()
        assert len(result) == 1

    def test_skips_dir_without_profile(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00010").mkdir()
        result = _ORIG_load_all_employees()
        assert len(result) == 0

    def test_nonexistent_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path / "nonexistent")
        result = _ORIG_load_all_employees()
        assert result == {}

    def test_cache_returns_deep_copy(self, tmp_path, monkeypatch):
        """Cached reads return deep copies so callers cannot poison the cache."""
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        (d / "profile.yaml").write_text("name: X\n")
        r1 = _ORIG_load_all_employees()  # populates cache
        r2 = _ORIG_load_all_employees()  # deep copy from cache
        r2["00010"]["name"] = "MUTATED"
        r3 = _ORIG_load_all_employees()  # another deep copy
        assert r3["00010"]["name"] == "X"


class TestLoadExEmployees:
    def test_reads_ex_employee_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EX_EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00050"
        d.mkdir()
        (d / "profile.yaml").write_text("name: Former\n")
        result = _ORIG_load_ex_employees()
        assert "00050" in result
        assert result["00050"]["name"] == "Former"

    def test_nonexistent_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EX_EMPLOYEES_DIR", tmp_path / "nope")
        assert _ORIG_load_ex_employees() == {}

    def test_skips_non_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EX_EMPLOYEES_DIR", tmp_path)
        (tmp_path / "README.md").write_text("ignore")
        d = tmp_path / "00050"
        d.mkdir()
        (d / "profile.yaml").write_text("name: X\n")
        result = _ORIG_load_ex_employees()
        assert len(result) == 1

    def test_skips_dir_without_profile(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EX_EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00050").mkdir()
        result = _ORIG_load_ex_employees()
        assert len(result) == 0


class TestLoadEmployeeGuidance:
    def test_dict_with_notes_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        (d / "guidance.yaml").write_text("notes:\n  - be kind\n  - be fast\n")
        result = _ORIG_load_employee_guidance("00010")
        assert result == ["be kind", "be fast"]

    def test_list_format(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        (d / "guidance.yaml").write_text("- note1\n- note2\n")
        result = _ORIG_load_employee_guidance("00010")
        assert result == ["note1", "note2"]

    def test_empty_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        result = _ORIG_load_employee_guidance("00010")
        assert result == []

    def test_non_dict_non_list_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        (d / "guidance.yaml").write_text("just a string\n")
        result = _ORIG_load_employee_guidance("00010")
        assert result == []


class TestLoadEmployeeWorkPrinciples:
    def test_reads_md_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        (d / "work_principles.md").write_text("Be awesome")
        assert _ORIG_load_employee_work_principles("00010") == "Be awesome"

    def test_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00010").mkdir()
        assert _ORIG_load_employee_work_principles("00010") == ""

    def test_exception_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        wp = d / "work_principles.md"
        wp.write_text("content")
        # Make Path.read_text fail for work_principles files
        orig_read_text = Path.read_text

        def bad_read_text(self, **kw):
            if "work_principles" in str(self):
                raise OSError("read fail")
            return orig_read_text(self, **kw)

        monkeypatch.setattr(Path, "read_text", bad_read_text)
        assert _ORIG_load_employee_work_principles("00010") == ""


# ---------------------------------------------------------------------------
# Employee writes — call originals to bypass conftest bridge
# ---------------------------------------------------------------------------

class TestSaveEmployee:
    @pytest.mark.asyncio
    async def test_merges_updates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00100"
        d.mkdir()
        (d / "profile.yaml").write_text("name: Old\nskills:\n- python\n")
        await _ORIG_save_employee("00100", {"name": "New", "level": 2})
        data = yaml.safe_load((d / "profile.yaml").read_text())
        assert data["name"] == "New"
        assert data["level"] == 2
        assert data["skills"] == ["python"]


class TestSaveEmployeeRuntime:
    @pytest.mark.asyncio
    async def test_creates_runtime_section(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00100"
        d.mkdir()
        (d / "profile.yaml").write_text("name: Bot\n")
        await _ORIG_save_employee_runtime("00100", status="working")
        data = yaml.safe_load((d / "profile.yaml").read_text())
        assert data["runtime"]["status"] == "working"


class TestSaveExEmployee:
    @pytest.mark.asyncio
    async def test_writes_profile(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EX_EMPLOYEES_DIR", tmp_path)
        await _ORIG_save_ex_employee("00050", {"name": "Gone", "reason": "quit"})
        data = yaml.safe_load((tmp_path / "00050" / "profile.yaml").read_text())
        assert data["name"] == "Gone"
        assert "ex_employees" in _store_mod._dirty


class TestSaveGuidance:
    @pytest.mark.asyncio
    async def test_writes_guidance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        await _ORIG_save_guidance("00010", ["be good", "be fast"])
        data = yaml.safe_load((d / "guidance.yaml").read_text())
        assert data["notes"] == ["be good", "be fast"]


class TestSaveWorkPrinciples:
    @pytest.mark.asyncio
    async def test_writes_md(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        await _ORIG_save_work_principles("00010", "Be excellent\nTo each other")
        text = (d / "work_principles.md").read_text()
        assert "Be excellent" in text


# ---------------------------------------------------------------------------
# Room reads/writes
# ---------------------------------------------------------------------------

class TestRooms:
    def test_rooms_dir_returns_path(self):
        from onemancompany.core.store import _rooms_dir, ROOMS_DIR
        assert _rooms_dir() == ROOMS_DIR

    def test_load_rooms_reads_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        (tmp_path / "room-a.yaml").write_text("name: Alpha\ncapacity: 4\n")
        (tmp_path / "room-b.yaml").write_text("name: Beta\n")
        result = _store_mod.load_rooms()
        assert len(result) == 2

    def test_load_rooms_excludes_chat_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        (tmp_path / "room-a.yaml").write_text("name: Alpha\n")
        (tmp_path / "room-a_chat.yaml").write_text("- msg: hello\n")
        result = _store_mod.load_rooms()
        assert len(result) == 1

    def test_load_rooms_nonexistent_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path / "nope")
        assert _store_mod.load_rooms() == []

    def test_load_rooms_sets_default_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        (tmp_path / "room-x.yaml").write_text("name: X\n")
        result = _store_mod.load_rooms()
        assert result[0]["id"] == "room-x"

    def test_load_room(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        (tmp_path / "room-a.yaml").write_text("name: Alpha\n")
        result = _store_mod.load_room("room-a")
        assert result["name"] == "Alpha"
        assert result["id"] == "room-a"

    def test_load_room_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        assert _store_mod.load_room("missing") == {}

    @pytest.mark.asyncio
    async def test_save_room(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        (tmp_path / "room-a.yaml").write_text("name: Alpha\n")
        await _ORIG_save_room("room-a", {"booked": True})
        data = yaml.safe_load((tmp_path / "room-a.yaml").read_text())
        assert data["booked"] is True
        assert data["name"] == "Alpha"

    def test_load_room_chat(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        (tmp_path / "room-a_chat.yaml").write_text("- msg: hello\n- msg: world\n")
        result = _store_mod.load_room_chat("room-a")
        assert len(result) == 2

    def test_load_room_chat_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        assert _store_mod.load_room_chat("missing") == []

    @pytest.mark.asyncio
    async def test_append_room_chat(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        await _ORIG_append_room_chat("room-a", {"sender": "CEO", "text": "hi"})
        await _ORIG_append_room_chat("room-a", {"sender": "CTO", "text": "hey"})
        result = _store_mod.load_room_chat("room-a")
        assert len(result) == 2
        assert result[0]["sender"] == "CEO"

    @pytest.mark.asyncio
    async def test_clear_room_chat(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        (tmp_path / "room-a_chat.yaml").write_text("- msg: old\n")
        await _ORIG_clear_room_chat("room-a")
        result = _store_mod.load_room_chat("room-a")
        assert result == []


# ---------------------------------------------------------------------------
# Meeting minutes
# ---------------------------------------------------------------------------

class TestMeetingMinutes:
    def test_archive_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "MEETING_MINUTES_DIR", tmp_path)
        minute_id = _store_mod.archive_meeting("room-a", {"topic": "standup", "messages": []})
        assert minute_id.startswith("room-a_")
        result = _store_mod.load_meeting_minutes()
        assert len(result) == 1
        assert result[0]["topic"] == "standup"

    def test_load_meeting_minutes_filtered(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "MEETING_MINUTES_DIR", tmp_path)
        _store_mod.archive_meeting("room-a", {"topic": "a"})
        _store_mod.archive_meeting("room-b", {"topic": "b"})
        result = _store_mod.load_meeting_minutes(room_id="room-a")
        assert len(result) == 1
        assert result[0]["topic"] == "a"

    def test_load_meeting_minutes_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "MEETING_MINUTES_DIR", tmp_path / "nope")
        assert _store_mod.load_meeting_minutes() == []

    def test_load_meeting_minutes_skips_non_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "MEETING_MINUTES_DIR", tmp_path)
        (tmp_path / "readme.txt").write_text("ignore")
        _write_yaml(tmp_path / "room-a_123.yaml", {"topic": "a"})
        result = _store_mod.load_meeting_minutes()
        assert len(result) == 1

    def test_load_meeting_minutes_skips_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "MEETING_MINUTES_DIR", tmp_path)
        (tmp_path / "room-a_123.yaml").write_text("")
        result = _store_mod.load_meeting_minutes()
        assert len(result) == 0

    def test_load_meeting_minute_single(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "MEETING_MINUTES_DIR", tmp_path)
        _write_yaml(tmp_path / "room-a_001.yaml", {"topic": "standup"})
        result = _store_mod.load_meeting_minute("room-a_001")
        assert result["topic"] == "standup"

    def test_load_meeting_minute_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "MEETING_MINUTES_DIR", tmp_path)
        assert _store_mod.load_meeting_minute("missing") == {}


# ---------------------------------------------------------------------------
# Project reads/writes
# ---------------------------------------------------------------------------

class TestProject:
    def test_load_project(self, monkeypatch):
        monkeypatch.setattr(
            "onemancompany.core.project_archive.load_project",
            lambda pid: {"id": pid, "name": "Test"} if pid == "p1" else None,
        )
        assert _store_mod.load_project("p1")["name"] == "Test"
        assert _store_mod.load_project("p999") == {}

    @pytest.mark.asyncio
    async def test_save_project_status(self, tmp_path, monkeypatch):
        called = {}

        def fake_update(pid, status, **extra):
            called["pid"] = pid
            called["status"] = status
            called.update(extra)

        monkeypatch.setattr(
            "onemancompany.core.project_archive.update_project_status",
            fake_update,
        )
        await _store_mod.save_project_status("p1", "completed", note="done")
        assert called["pid"] == "p1"
        assert called["status"] == "completed"
        assert called["note"] == "done"


# ---------------------------------------------------------------------------
# Tool reads/writes
# ---------------------------------------------------------------------------

class TestTools:
    def test_load_tools(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "DATA_ROOT", tmp_path)
        tools_dir = tmp_path / "company" / "assets" / "tools"
        t = tools_dir / "tool-a"
        t.mkdir(parents=True)
        (t / "tool.yaml").write_text("name: Hammer\n")
        result = _store_mod.load_tools()
        assert len(result) == 1
        assert result[0]["name"] == "Hammer"

    def test_load_tools_nonexistent_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "DATA_ROOT", tmp_path)
        assert _store_mod.load_tools() == []

    def test_load_tools_skips_non_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "DATA_ROOT", tmp_path)
        tools_dir = tmp_path / "company" / "assets" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "readme.txt").write_text("ignore")
        t = tools_dir / "tool-a"
        t.mkdir()
        (t / "tool.yaml").write_text("name: Hammer\n")
        result = _store_mod.load_tools()
        assert len(result) == 1

    def test_load_tools_skips_dir_without_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "DATA_ROOT", tmp_path)
        tools_dir = tmp_path / "company" / "assets" / "tools"
        (tools_dir / "empty-tool").mkdir(parents=True)
        result = _store_mod.load_tools()
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_save_tool(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "DATA_ROOT", tmp_path)
        tools_dir = tmp_path / "company" / "assets" / "tools"
        t = tools_dir / "my-tool"
        t.mkdir(parents=True)
        await _store_mod.save_tool("my-tool", {"name": "New"})
        data = yaml.safe_load((t / "tool.yaml").read_text())
        assert data["name"] == "New"


# ---------------------------------------------------------------------------
# Task tree
# ---------------------------------------------------------------------------

class TestSaveTree:
    @pytest.mark.asyncio
    async def test_writes_yaml(self, tmp_path):
        pdir = tmp_path / "proj-001"
        pdir.mkdir()
        await _store_mod.save_tree(str(pdir), {"root": "n1"})
        data = yaml.safe_load((pdir / "task_tree.yaml").read_text())
        assert data["root"] == "n1"


# ---------------------------------------------------------------------------
# Company-level reads/writes
# ---------------------------------------------------------------------------

class TestActivityLog:
    def test_load_activity_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        (tmp_path / "activity_log.yaml").write_text("- type: hired\n")
        result = _ORIG_load_activity_log()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_append_activity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        await _ORIG_append_activity({"type": "hired"})
        await _ORIG_append_activity({"type": "fired"})
        data = _read_yaml_list(tmp_path / "activity_log.yaml")
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_append_activity_truncates_at_200(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        entries = [{"type": f"entry-{i}"} for i in range(200)]
        _atomic_write_text(
            tmp_path / "activity_log.yaml",
            yaml.dump(entries, allow_unicode=True),
        )
        await _ORIG_append_activity({"type": "new"})
        data = _read_yaml_list(tmp_path / "activity_log.yaml")
        assert len(data) == 200
        assert data[-1]["type"] == "new"

    def test_append_activity_sync(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        _ORIG_append_activity_sync({"type": "sync-test"})
        data = _read_yaml_list(tmp_path / "activity_log.yaml")
        assert len(data) == 1
        assert data[0]["type"] == "sync-test"

    def test_append_activity_sync_truncates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        entries = [{"type": f"e-{i}"} for i in range(200)]
        _atomic_write_text(
            tmp_path / "activity_log.yaml",
            yaml.dump(entries, allow_unicode=True),
        )
        _ORIG_append_activity_sync({"type": "overflow"})
        data = _read_yaml_list(tmp_path / "activity_log.yaml")
        assert len(data) == 200
        assert data[-1]["type"] == "overflow"


class TestCulture:
    def test_load_culture(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        (tmp_path / "company_culture.yaml").write_text("- value: Move fast\n")
        result = _ORIG_load_culture()
        assert len(result) == 1
        assert result[0]["value"] == "Move fast"

    @pytest.mark.asyncio
    async def test_save_culture(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        await _store_mod.save_culture([{"value": "Move fast"}])
        data = _read_yaml_list(tmp_path / "company_culture.yaml")
        assert data[0]["value"] == "Move fast"


class TestDirection:
    def test_load_direction(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIRECTION_FILE", tmp_path / "dir.yaml")
        _write_yaml(tmp_path / "dir.yaml", {"direction": "go north"})
        assert _ORIG_load_direction() == "go north"

    def test_load_direction_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIRECTION_FILE", tmp_path / "nope.yaml")
        assert _ORIG_load_direction() == ""

    def test_load_direction_empty_data(self, tmp_path, monkeypatch):
        p = tmp_path / "dir.yaml"
        p.write_text("")
        monkeypatch.setattr(_store_mod, "COMPANY_DIRECTION_FILE", p)
        assert _ORIG_load_direction() == ""

    @pytest.mark.asyncio
    async def test_save_direction(self, tmp_path, monkeypatch):
        p = tmp_path / "dir.yaml"
        monkeypatch.setattr(_store_mod, "COMPANY_DIRECTION_FILE", p)
        await _store_mod.save_direction("go east")
        data = _read_yaml(p)
        assert data["direction"] == "go east"
        assert "direction" in _store_mod._dirty


class TestSalesTasks:
    def test_load_sales_tasks(self, tmp_path, monkeypatch):
        path = tmp_path / "tasks.yaml"
        path.write_text("- id: t1\n  title: Sell\n")
        monkeypatch.setattr(_store_mod, "SALES_TASKS_PATH", path)
        result = _store_mod.load_sales_tasks()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_save_sales_tasks(self, tmp_path, monkeypatch):
        path = tmp_path / "sales" / "tasks.yaml"
        monkeypatch.setattr(_store_mod, "SALES_TASKS_PATH", path)
        await _store_mod.save_sales_tasks([{"id": "t1"}])
        data = _read_yaml_list(path)
        assert data[0]["id"] == "t1"

    def test_save_sales_tasks_sync(self, tmp_path, monkeypatch):
        path = tmp_path / "sales" / "tasks.yaml"
        monkeypatch.setattr(_store_mod, "SALES_TASKS_PATH", path)
        _store_mod.save_sales_tasks_sync([{"id": "t2"}])
        data = _read_yaml_list(path)
        assert data[0]["id"] == "t2"


# ---------------------------------------------------------------------------
# Task index — use original functions to bypass conftest no-op
# ---------------------------------------------------------------------------

class TestTaskIndex:
    """Task index tests.

    The conftest patches load_task_index, save_task_index, and
    append_task_index_entry to no-ops. We must restore the originals
    on the module so internal calls within these functions work.
    """

    @pytest.fixture(autouse=True)
    def _restore_task_index_fns(self, monkeypatch):
        """Restore original task index functions on the store module."""
        monkeypatch.setattr(_store_mod, "load_task_index", _ORIG_load_task_index)
        monkeypatch.setattr(_store_mod, "save_task_index", _ORIG_save_task_index)
        monkeypatch.setattr(_store_mod, "append_task_index_entry", _ORIG_append_task_index_entry)

    def test_load_task_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        (d / "task_index.yaml").write_text("- node_id: n1\n  tree_path: /p/t\n")
        result = _ORIG_load_task_index("00010")
        assert len(result) == 1
        assert result[0]["node_id"] == "n1"

    def test_save_task_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        _ORIG_save_task_index("00010", [{"node_id": "n1", "tree_path": "/p/t"}])
        data = _read_yaml_list(d / "task_index.yaml")
        assert len(data) == 1

    def test_append_task_index_entry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        _ORIG_append_task_index_entry("00010", "n1", "/p/t1")
        _ORIG_append_task_index_entry("00010", "n2", "/p/t2")
        data = _read_yaml_list(d / "task_index.yaml")
        assert len(data) == 2

    def test_append_task_index_entry_deduplicates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        _ORIG_append_task_index_entry("00010", "n1", "/p/t1")
        _ORIG_append_task_index_entry("00010", "n1", "/p/t1")
        data = _read_yaml_list(d / "task_index.yaml")
        assert len(data) == 1

    def test_append_task_index_entry_caps_at_100(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        entries = [{"node_id": f"n{i}", "tree_path": f"/p/t{i}"} for i in range(100)]
        _ORIG_save_task_index("00010", entries)
        _ORIG_append_task_index_entry("00010", "n999", "/p/t999")
        data = _read_yaml_list(d / "task_index.yaml")
        assert len(data) == 100
        assert data[-1]["node_id"] == "n999"


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

class TestCandidates:
    def test_load_candidates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        cdir = tmp_path / "candidates"
        cdir.mkdir()
        _write_yaml(cdir / "batch-1.yaml", {"candidates": [{"name": "Alice"}]})
        result = _store_mod.load_candidates("batch-1")
        assert result["candidates"][0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_save_candidates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        await _store_mod.save_candidates("batch-1", {"candidates": [{"name": "Bob"}]})
        data = _read_yaml(tmp_path / "candidates" / "batch-1.yaml")
        assert data["candidates"][0]["name"] == "Bob"


# ---------------------------------------------------------------------------
# 1-on-1 history
# ---------------------------------------------------------------------------

class TestOneonone:
    def test_load_oneonone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        (d / "oneonone_history.yaml").write_text("- sender: CEO\n  text: hi\n")
        result = _store_mod.load_oneonone("00010")
        assert len(result) == 1

    def test_load_oneonone_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00010").mkdir()
        assert _store_mod.load_oneonone("00010") == []

    @pytest.mark.asyncio
    async def test_append_oneonone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        await _ORIG_append_oneonone("00010", {"sender": "CEO", "text": "hello"})
        await _ORIG_append_oneonone("00010", {"sender": "EMP", "text": "hi"})
        result = _store_mod.load_oneonone("00010")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Overhead
# ---------------------------------------------------------------------------

class TestOverhead:
    def test_load_overhead(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        _write_yaml(tmp_path / "overhead.yaml", {"tokens": 1000})
        result = _store_mod.load_overhead()
        assert result["tokens"] == 1000

    @pytest.mark.asyncio
    async def test_save_overhead(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        await _store_mod.save_overhead({"tokens": 2000})
        data = _read_yaml(tmp_path / "overhead.yaml")
        assert data["tokens"] == 2000

    def test_save_overhead_sync(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        _store_mod.save_overhead_sync({"tokens": 3000})
        data = _read_yaml(tmp_path / "overhead.yaml")
        assert data["tokens"] == 3000
        assert "overhead" in _store_mod._dirty


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------

class TestAsyncWrappers:
    @pytest.mark.asyncio
    async def test_aload_all_employees(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "EMPLOYEES_DIR", tmp_path)
        d = tmp_path / "00010"
        d.mkdir()
        (d / "profile.yaml").write_text("name: Bob\n")
        result = await _store_mod.aload_all_employees()
        assert "00010" in result

    @pytest.mark.asyncio
    async def test_aload_activity_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        (tmp_path / "activity_log.yaml").write_text("- type: test\n")
        result = await _store_mod.aload_activity_log()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_aload_tools(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "DATA_ROOT", tmp_path)
        tools_dir = tmp_path / "company" / "assets" / "tools" / "saw"
        tools_dir.mkdir(parents=True)
        (tools_dir / "tool.yaml").write_text("name: Saw\n")
        result = await _store_mod.aload_tools()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_aload_rooms(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "_rooms_dir", lambda: tmp_path)
        (tmp_path / "room-a.yaml").write_text("name: A\n")
        result = await _store_mod.aload_rooms()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_aload_overhead(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_store_mod, "COMPANY_DIR", tmp_path)
        _write_yaml(tmp_path / "overhead.yaml", {"tokens": 500})
        result = await _store_mod.aload_overhead()
        assert result["tokens"] == 500


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_employee_profile_path(self):
        from onemancompany.core.store import _employee_profile_path, EMPLOYEES_DIR
        assert _employee_profile_path("00100") == EMPLOYEES_DIR / "00100" / "profile.yaml"

    def test_ex_employee_profile_path(self):
        from onemancompany.core.store import _ex_employee_profile_path, EX_EMPLOYEES_DIR
        assert _ex_employee_profile_path("00050") == EX_EMPLOYEES_DIR / "00050" / "profile.yaml"


# ---------------------------------------------------------------------------
# Legacy tests (preserved from original file)
# ---------------------------------------------------------------------------

class TestAbortUsesTransition:
    """Abort paths must not bypass the state machine."""

    def test_no_direct_status_assignment_in_vessel_abort(self):
        import inspect
        from onemancompany.core.vessel import EmployeeManager
        source = inspect.getsource(EmployeeManager.abort_project)
        assert "node.status = TaskPhase.CANCELLED" not in source

    def test_no_direct_status_assignment_in_routes_abort(self):
        routes_path = Path(__file__).parent.parent.parent.parent / "src/onemancompany/api/routes.py"
        source = routes_path.read_text()
        count = source.count("node.status = TaskPhase.CANCELLED.value")
        assert count == 0

    def test_safe_cancel_accepted_returns_false(self):
        from onemancompany.core.task_lifecycle import safe_cancel, TaskPhase
        node = MagicMock()
        node.status = TaskPhase.ACCEPTED.value
        assert safe_cancel(node) is False

    def test_safe_cancel_pending_returns_true(self):
        from onemancompany.core.task_lifecycle import safe_cancel, TaskPhase
        node = MagicMock()
        node.status = TaskPhase.PENDING.value
        result = safe_cancel(node)
        assert result is True
        node.set_status.assert_called_once_with(TaskPhase.CANCELLED)
