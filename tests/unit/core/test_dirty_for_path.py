"""Tests for mark_dirty_for_path — auto-detect dirty category from file path."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from onemancompany.core.config import DirtyCategory


class TestMarkDirtyForPath:
    @pytest.fixture(autouse=True)
    def _reset_registry(self):
        """Reset the cached path→dirty registry between tests."""
        import onemancompany.core.store as _store
        _store._path_dirty_registry = None
        yield
        _store._path_dirty_registry = None

    def test_employee_file_marks_employees(self, tmp_path):
        emp_dir = tmp_path / "employees"
        emp_dir.mkdir()
        target = emp_dir / "00004" / "work_principles.md"
        target.parent.mkdir()
        target.write_text("hello")

        from onemancompany.core.store import mark_dirty_for_path
        with patch("onemancompany.core.config.EMPLOYEES_DIR", emp_dir), \
             patch("onemancompany.core.store.mark_dirty") as mock:
            mark_dirty_for_path(target)
            mock.assert_called_once_with(DirtyCategory.EMPLOYEES)

    def test_rooms_file_marks_rooms(self, tmp_path):
        rooms_dir = tmp_path / "rooms"
        rooms_dir.mkdir()
        target = rooms_dir / "meeting.yaml"
        target.write_text("room")

        from onemancompany.core.store import mark_dirty_for_path
        with patch("onemancompany.core.config.ROOMS_DIR", rooms_dir), \
             patch("onemancompany.core.store.mark_dirty") as mock:
            mark_dirty_for_path(target)
            mock.assert_called_once_with(DirtyCategory.ROOMS)

    def test_ex_employee_file_marks_ex_employees(self, tmp_path):
        ex_dir = tmp_path / "ex-employees"
        ex_dir.mkdir()
        target = ex_dir / "00099" / "profile.yaml"
        target.parent.mkdir()
        target.write_text("data")

        from onemancompany.core.store import mark_dirty_for_path
        with patch("onemancompany.core.config.EX_EMPLOYEES_DIR", ex_dir), \
             patch("onemancompany.core.store.mark_dirty") as mock:
            mark_dirty_for_path(target)
            mock.assert_called_once_with(DirtyCategory.EX_EMPLOYEES)

    def test_unrelated_path_no_dirty(self, tmp_path):
        target = tmp_path / "random" / "file.txt"
        target.parent.mkdir()
        target.write_text("data")

        from onemancompany.core.store import mark_dirty_for_path
        with patch("onemancompany.core.store.mark_dirty") as mock:
            mark_dirty_for_path(target)
            mock.assert_not_called()

    def test_tools_file_marks_tools(self, tmp_path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        target = tools_dir / "my_tool" / "tool.yaml"
        target.parent.mkdir()
        target.write_text("tool")

        from onemancompany.core.store import mark_dirty_for_path
        with patch("onemancompany.core.config.TOOLS_DIR", tools_dir), \
             patch("onemancompany.core.store.mark_dirty") as mock:
            mark_dirty_for_path(target)
            mock.assert_called_once_with(DirtyCategory.TOOLS)

    def test_most_specific_match_wins(self, tmp_path):
        """EMPLOYEES_DIR is under HR_DIR. Employee file should match EMPLOYEES, not a parent."""
        hr_dir = tmp_path / "hr"
        emp_dir = hr_dir / "employees"
        emp_dir.mkdir(parents=True)
        target = emp_dir / "00004" / "profile.yaml"
        target.parent.mkdir()
        target.write_text("data")

        from onemancompany.core.store import mark_dirty_for_path
        with patch("onemancompany.core.config.EMPLOYEES_DIR", emp_dir), \
             patch("onemancompany.core.store.mark_dirty") as mock:
            mark_dirty_for_path(target)
            mock.assert_called_once_with(DirtyCategory.EMPLOYEES)
