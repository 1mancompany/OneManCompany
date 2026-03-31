"""Regression test: write/edit tools call mark_dirty_for_path after file writes."""
from __future__ import annotations

import inspect

import pytest


class TestWriteEditCallMarkDirtyForPath:
    """Structural test: verify write() and edit() call mark_dirty_for_path."""

    def test_write_calls_mark_dirty_for_path(self):
        from onemancompany.agents.common_tools import write
        source = inspect.getsource(write.coroutine)
        assert "mark_dirty_for_path" in source, \
            "write() tool must call mark_dirty_for_path after writing"

    def test_edit_calls_mark_dirty_for_path(self):
        from onemancompany.agents.common_tools import edit
        source = inspect.getsource(edit.coroutine)
        assert "mark_dirty_for_path" in source, \
            "edit() tool must call mark_dirty_for_path after editing"
