"""Unit tests for store.py read cache and async wrappers."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from onemancompany.core.config import DirtyCategory
from onemancompany.core.store import (
    _CACHE_TTL_SECONDS,
    _read_cache,
    _read_cache_get,
    _read_cache_invalidate,
    _read_cache_set,
    cache_clear_all,
    mark_dirty,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure each test starts with a clean cache."""
    cache_clear_all()
    yield
    cache_clear_all()


class TestReadCache:
    def test_set_and_get(self):
        _read_cache_set("k1", {"a": 1}, "cat_a")
        assert _read_cache_get("k1") == {"a": 1}

    def test_miss_returns_none(self):
        assert _read_cache_get("nonexistent") is None

    def test_ttl_expiry(self):
        _read_cache_set("k2", "val", "cat_b")
        # Manually age the entry
        key_val, _ = _read_cache["k2"]
        _read_cache["k2"] = (key_val, time.monotonic() - _CACHE_TTL_SECONDS - 1)
        assert _read_cache_get("k2") is None

    def test_invalidate_by_category(self):
        _read_cache_set("k3", "v3", "cat_c")
        _read_cache_set("k4", "v4", "cat_c")
        _read_cache_set("k5", "v5", "cat_d")
        _read_cache_invalidate("cat_c")
        assert _read_cache_get("k3") is None
        assert _read_cache_get("k4") is None
        assert _read_cache_get("k5") == "v5"

    def test_mark_dirty_invalidates_cache(self):
        _read_cache_set("all_employees", {"e1": {}}, DirtyCategory.EMPLOYEES)
        assert _read_cache_get("all_employees") is not None
        mark_dirty(DirtyCategory.EMPLOYEES)
        assert _read_cache_get("all_employees") is None

    def test_cache_clear_all(self):
        _read_cache_set("a", 1, "x")
        _read_cache_set("b", 2, "y")
        cache_clear_all()
        assert _read_cache_get("a") is None
        assert _read_cache_get("b") is None


class TestCachedLoadFunctions:
    """Test that load_all_employees / load_tools / load_activity_log use cache."""

    def test_load_all_employees_caches(self, tmp_path):
        from onemancompany.core.store import load_all_employees

        with patch("onemancompany.core.store.EMPLOYEES_DIR", tmp_path):
            # Create a fake employee dir
            emp_dir = tmp_path / "00010"
            emp_dir.mkdir()
            (emp_dir / "profile.yaml").write_text("name: Alice\nrole: Engineer\n")
            (emp_dir / "work_principles.md").write_text("Be good")
            (emp_dir / "guidance.yaml").write_text("notes:\n  - note1\n")

            result1 = load_all_employees()
            assert "00010" in result1

            # Second call should hit cache (no disk read)
            # Verify by removing the file — cached result should still work
            (emp_dir / "profile.yaml").unlink()
            result2 = load_all_employees()
            assert "00010" in result2

    def test_load_tools_caches(self, tmp_path):
        from onemancompany.core.store import load_tools

        tools_dir = tmp_path / "company" / "assets" / "tools" / "hammer"
        tools_dir.mkdir(parents=True)
        (tools_dir / "tool.yaml").write_text("name: Hammer\ncategory: build\n")

        with patch("onemancompany.core.store.DATA_ROOT", tmp_path):
            result1 = load_tools()
            assert len(result1) == 1

            (tools_dir / "tool.yaml").unlink()
            result2 = load_tools()
            assert len(result2) == 1  # From cache

    def test_load_activity_log_caches(self, tmp_path):
        from onemancompany.core.store import load_activity_log

        log_path = tmp_path / "activity_log.yaml"
        log_path.write_text("- type: test\n  desc: hello\n")

        with patch("onemancompany.core.store.COMPANY_DIR", tmp_path):
            result1 = load_activity_log()
            assert len(result1) == 1

            log_path.unlink()
            result2 = load_activity_log()
            assert len(result2) == 1  # From cache


class TestAsyncWrappers:
    @pytest.mark.asyncio
    async def test_aload_all_employees(self, tmp_path):
        from onemancompany.core.store import aload_all_employees

        with patch("onemancompany.core.store.EMPLOYEES_DIR", tmp_path):
            emp_dir = tmp_path / "00010"
            emp_dir.mkdir()
            (emp_dir / "profile.yaml").write_text("name: Bob\n")
            (emp_dir / "work_principles.md").write_text("")
            (emp_dir / "guidance.yaml").write_text("")

            result = await aload_all_employees()
            assert "00010" in result

    @pytest.mark.asyncio
    async def test_aload_tools(self, tmp_path):
        from onemancompany.core.store import aload_tools

        tools_dir = tmp_path / "company" / "assets" / "tools" / "saw"
        tools_dir.mkdir(parents=True)
        (tools_dir / "tool.yaml").write_text("name: Saw\n")

        with patch("onemancompany.core.store.DATA_ROOT", tmp_path):
            result = await aload_tools()
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_aload_activity_log(self, tmp_path):
        from onemancompany.core.store import aload_activity_log

        log_path = tmp_path / "activity_log.yaml"
        log_path.write_text("- type: test\n")

        with patch("onemancompany.core.store.COMPANY_DIR", tmp_path):
            result = await aload_activity_log()
            assert len(result) == 1
