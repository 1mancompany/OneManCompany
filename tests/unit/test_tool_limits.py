"""Tests for tool result size limiting with disk persistence."""

import re
from pathlib import Path

import pytest

from onemancompany.core.tool_limits import (
    DEFAULT_MAX_RESULT_SIZE,
    PREVIEW_SIZE,
    maybe_persist_result,
)


class TestMaybePersistResult:
    def test_small_result_unchanged(self):
        result = "hello world"
        assert maybe_persist_result(result, "test") == "hello world"

    def test_at_boundary_unchanged(self):
        result = "x" * DEFAULT_MAX_RESULT_SIZE
        assert maybe_persist_result(result, "test") == result

    def test_large_result_persisted(self, tmp_path):
        large = "x" * (DEFAULT_MAX_RESULT_SIZE + 1000)
        result = maybe_persist_result(large, "test_tool", project_dir=str(tmp_path))
        assert "Output too large" in result
        assert "saved to:" in result
        assert "Preview" in result
        assert len(result) < DEFAULT_MAX_RESULT_SIZE

    def test_persisted_file_exists(self, tmp_path):
        large = "line\n" * 20000
        result = maybe_persist_result(large, "bash", project_dir=str(tmp_path))
        # Extract filepath from result
        match = re.search(r"saved to: (.+\.txt)", result)
        assert match
        filepath = Path(match.group(1))
        assert filepath.exists()
        assert filepath.read_text() == large

    def test_project_dir_used_when_provided(self, tmp_path):
        large = "x" * (DEFAULT_MAX_RESULT_SIZE + 100)
        result = maybe_persist_result(
            large, "read", project_dir=str(tmp_path)
        )
        assert str(tmp_path / "tool_results") in result

    def test_preview_size(self, tmp_path):
        large = "A" * (DEFAULT_MAX_RESULT_SIZE + 5000)
        result = maybe_persist_result(large, "test", project_dir=str(tmp_path))
        # Preview should contain first PREVIEW_SIZE chars of original
        assert "A" * min(PREVIEW_SIZE, 100) in result

    def test_empty_string_unchanged(self):
        assert maybe_persist_result("", "test") == ""

    def test_large_result_no_project_dir(self, tmp_path, monkeypatch):
        """When project_dir is None, falls back to DATA_ROOT/tool_results."""
        monkeypatch.setattr("onemancompany.core.config.DATA_ROOT", tmp_path)
        large = "x" * (DEFAULT_MAX_RESULT_SIZE + 100)
        result = maybe_persist_result(large, "test_tool")
        assert "Output too large" in result
        assert (tmp_path / "tool_results").exists()
