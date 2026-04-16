"""Coverage tests for agents/common_tools.py — missing lines."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _resolve_employee_path (lines 95, 138-141)
# ---------------------------------------------------------------------------

class TestResolveEmployeePath:
    def test_workspace_prefix(self, tmp_path, monkeypatch):
        from onemancompany.agents.common_tools import _resolve_employee_path
        with patch("onemancompany.agents.common_tools.get_workspace_dir", return_value=tmp_path):
            result = _resolve_employee_path("workspace/readme.md", "00010")
        assert result is not None
        assert str(tmp_path) in str(result)

    def test_absolute_path(self, tmp_path):
        from onemancompany.agents.common_tools import _resolve_employee_path
        result = _resolve_employee_path(str(tmp_path / "test.txt"))
        assert result == (tmp_path / "test.txt").resolve()


# ---------------------------------------------------------------------------
# read tool — offset/limit (lines 175-176, 179)
# ---------------------------------------------------------------------------

class TestReadTool:
    def test_read_with_offset_and_limit(self, tmp_path, monkeypatch):
        import onemancompany.agents.common_tools as ct_mod
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5\n")

        with patch.object(ct_mod, "_resolve_employee_path", return_value=test_file):
            result = ct_mod.read.invoke({
                "file_path": str(test_file), "employee_id": "00010",
                "offset": 2, "limit": 2,
            })
        assert result["status"] == "ok"
        assert "line2" in result["content"]
        assert "line3" in result["content"]

    def test_read_file_not_found(self, tmp_path, monkeypatch):
        import onemancompany.agents.common_tools as ct_mod
        with patch.object(ct_mod, "_resolve_employee_path", return_value=tmp_path / "missing.txt"):
            result = ct_mod.read.invoke({
                "file_path": "missing.txt", "employee_id": "00010",
            })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# ls tool — workspace path + entries (lines 231, 238-240, 256-259)
# ---------------------------------------------------------------------------

class TestLsTool:
    def test_ls_workspace(self, tmp_path, monkeypatch):
        import onemancompany.agents.common_tools as ct_mod
        (tmp_path / "file1.txt").write_text("hi")
        (tmp_path / ".hidden").write_text("hidden")

        with patch("onemancompany.agents.common_tools.get_workspace_dir", return_value=tmp_path):
            result = ct_mod.ls.invoke({
                "dir_path": "workspace", "employee_id": "00010",
            })
        assert result["status"] == "ok"
        names = [e["name"] for e in result["entries"]]
        assert "file1.txt" in names
        assert ".hidden" not in names

    def test_ls_dir_not_found(self, tmp_path, monkeypatch):
        import onemancompany.agents.common_tools as ct_mod
        with patch.object(ct_mod, "_resolve_employee_path", return_value=tmp_path / "nonexistent"):
            result = ct_mod.ls.invoke({
                "dir_path": "/nonexistent", "employee_id": "00010",
            })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# write tool — safety checks (lines 287-319)
# ---------------------------------------------------------------------------

class TestWriteTool:
    @pytest.mark.asyncio
    async def test_write_creates_new_file(self, tmp_path, monkeypatch):
        import onemancompany.agents.common_tools as ct_mod
        target = tmp_path / "new_file.txt"

        with patch.object(ct_mod, "_resolve_employee_path", return_value=target), \
             patch("onemancompany.core.store.mark_dirty_for_path"):
            result = await ct_mod.write.ainvoke({
                "file_path": str(target), "content": "hello",
                "employee_id": "00010",
            })
        assert result["status"] == "ok"
        assert result["type"] == "create"

    @pytest.mark.asyncio
    async def test_write_must_read_first(self, tmp_path, monkeypatch):
        import onemancompany.agents.common_tools as ct_mod
        target = tmp_path / "existing.txt"
        target.write_text("old content")
        ct_mod._files_read_by_employee.pop("00010", None)

        with patch.object(ct_mod, "_resolve_employee_path", return_value=target):
            result = await ct_mod.write.ainvoke({
                "file_path": str(target), "content": "new",
                "employee_id": "00010",
            })
        assert result["status"] == "error"
        assert "read" in result.get("message", "").lower() or "retry" in str(result).lower()


# ---------------------------------------------------------------------------
# edit tool — missing/no changes (lines 347-373)
# ---------------------------------------------------------------------------

class TestEditTool:
    @pytest.mark.asyncio
    async def test_edit_file_not_found(self, tmp_path, monkeypatch):
        import onemancompany.agents.common_tools as ct_mod
        with patch.object(ct_mod, "_resolve_employee_path", return_value=tmp_path / "missing.txt"):
            result = await ct_mod.edit.ainvoke({
                "file_path": "missing.txt",
                "old_string": "foo", "new_string": "bar",
                "employee_id": "00010",
            })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# glob_files tool (lines 390-413)
# ---------------------------------------------------------------------------

class TestGlobFiles:
    def test_glob_basic(self, tmp_path, monkeypatch):
        import onemancompany.agents.common_tools as ct_mod
        (tmp_path / "a.py").write_text("# python")
        (tmp_path / "b.txt").write_text("text")

        with patch.object(ct_mod, "_resolve_employee_path", return_value=tmp_path):
            result = ct_mod.glob_files.invoke({
                "pattern": "*.py",
                "path": str(tmp_path),
                "employee_id": "00010",
            })
        assert result["status"] == "ok"
        assert any("a.py" in f for f in result.get("filenames", []))


# ---------------------------------------------------------------------------
# _validate_context (line 95)
# ---------------------------------------------------------------------------

class TestValidateEmployeeId:
    def test_empty_employee_id(self):
        from onemancompany.agents.common_tools import _validate_employee_id
        result = _validate_employee_id("")
        assert result is not None
        assert result["status"] == "error"

    def test_valid_employee_id(self):
        from onemancompany.agents.common_tools import _validate_employee_id
        result = _validate_employee_id("00010")
        assert result is None
