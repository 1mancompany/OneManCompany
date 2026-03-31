"""Regression test: write/edit to employee files must mark EMPLOYEES dirty.

Root cause: agents using write()/edit() tools to modify employee files
(e.g., work_principles.md) bypassed store.mark_dirty(), so the sync tick
never broadcast changes to the frontend.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from onemancompany.agents.common_tools import _mark_dirty_if_employee_path


class TestMarkDirtyIfEmployeePath:
    def test_employee_path_marks_dirty(self, tmp_path):
        emp_dir = tmp_path / "company" / "human_resource" / "employees"
        emp_dir.mkdir(parents=True)
        target = emp_dir / "00004" / "work_principles.md"
        target.parent.mkdir(parents=True)
        target.write_text("hello")

        with patch("onemancompany.core.config.EMPLOYEES_DIR", emp_dir), \
             patch("onemancompany.core.store.mark_dirty") as mock_dirty:
            _mark_dirty_if_employee_path(target)
            mock_dirty.assert_called_once()

    def test_non_employee_path_does_not_mark_dirty(self, tmp_path):
        emp_dir = tmp_path / "company" / "human_resource" / "employees"
        emp_dir.mkdir(parents=True)
        other = tmp_path / "company" / "business" / "projects" / "file.md"
        other.parent.mkdir(parents=True)
        other.write_text("hello")

        with patch("onemancompany.core.config.EMPLOYEES_DIR", emp_dir), \
             patch("onemancompany.core.store.mark_dirty") as mock_dirty:
            _mark_dirty_if_employee_path(other)
            mock_dirty.assert_not_called()


class TestWriteEditCallMarkDirty:
    """Structural test: verify write() and edit() call _mark_dirty_if_employee_path."""

    def test_write_calls_mark_dirty(self):
        from onemancompany.agents.common_tools import write
        source = inspect.getsource(write.coroutine)
        assert "_mark_dirty_if_employee_path" in source, \
            "write() tool must call _mark_dirty_if_employee_path after writing"

    def test_edit_calls_mark_dirty(self):
        from onemancompany.agents.common_tools import edit
        source = inspect.getsource(edit.coroutine)
        assert "_mark_dirty_if_employee_path" in source, \
            "edit() tool must call _mark_dirty_if_employee_path after editing"
