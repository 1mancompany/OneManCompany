"""Unit tests for core/file_editor.py — file editing with CEO approval."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core import file_editor
from onemancompany.core.file_editor import (
    BACKUP_FOLDER_NAME,
    _resolve_path,
    execute_edit,
    list_pending_edits,
    pending_file_edits,
    propose_edit,
    reject_edit,
)


@pytest.fixture(autouse=True)
def _clear_pending():
    """Clear pending edits before each test."""
    pending_file_edits.clear()
    yield
    pending_file_edits.clear()


# ---------------------------------------------------------------------------
# _resolve_path
# ---------------------------------------------------------------------------

class TestResolvePath:
    def test_relative_path_resolves_under_company_dir(self):
        result = _resolve_path("business/test.md")
        assert result is not None
        assert "company" in str(result)
        assert str(result).endswith("business/test.md")

    def test_company_prefix_stripped(self):
        result = _resolve_path("company/business/test.md")
        assert result is not None
        # Should NOT double up: company/company/business/test.md
        assert "company/company" not in str(result)

    def test_src_path_without_permission_denied(self):
        result = _resolve_path("src/onemancompany/core/config.py")
        assert result is None

    def test_src_path_with_permission_allowed(self):
        result = _resolve_path("src/onemancompany/core/config.py", permissions=["backend_code_maintenance"])
        assert result is not None
        assert "src/onemancompany/core/config.py" in str(result)

    def test_absolute_path_inside_company_allowed(self):
        from onemancompany.core.config import COMPANY_DIR
        p = str(COMPANY_DIR / "business" / "test.md")
        result = _resolve_path(p)
        assert result is not None

    def test_absolute_path_outside_project_denied(self):
        result = _resolve_path("/etc/passwd")
        assert result is None

    def test_invalid_path_returns_none(self):
        # Path with null bytes — OS-level error
        result = _resolve_path("/invalid\x00path")
        assert result is None


# ---------------------------------------------------------------------------
# propose_edit
# ---------------------------------------------------------------------------

class TestProposeEdit:
    def test_propose_valid_edit(self, tmp_path):
        with patch.object(file_editor, "COMPANY_DIR", tmp_path), \
             patch.object(file_editor, "PROJECT_ROOT", tmp_path.parent):
            # Create a file to edit
            target = tmp_path / "test.md"
            target.write_text("original content", encoding="utf-8")

            result = propose_edit("test.md", "new content", "fix typo", "00002")
            assert result["status"] == "pending_approval"
            assert "edit_id" in result
            assert result["rel_path"] == "test.md"

    def test_propose_edit_stores_in_pending(self, tmp_path):
        with patch.object(file_editor, "COMPANY_DIR", tmp_path), \
             patch.object(file_editor, "PROJECT_ROOT", tmp_path.parent):
            target = tmp_path / "test.md"
            target.write_text("old", encoding="utf-8")

            result = propose_edit("test.md", "new", "reason", "00002")
            edit_id = result["edit_id"]
            assert edit_id in pending_file_edits
            assert pending_file_edits[edit_id]["old_content"] == "old"
            assert pending_file_edits[edit_id]["new_content"] == "new"

    def test_propose_new_file(self, tmp_path):
        with patch.object(file_editor, "COMPANY_DIR", tmp_path), \
             patch.object(file_editor, "PROJECT_ROOT", tmp_path.parent):
            result = propose_edit("new_file.md", "content", "create", "00002")
            assert result["status"] == "pending_approval"
            edit_id = result["edit_id"]
            assert pending_file_edits[edit_id]["old_content"] == ""

    def test_propose_invalid_path(self):
        result = propose_edit("/etc/passwd", "evil", "hack", "bad")
        assert result["status"] == "error"
        assert "Invalid path" in result["message"]


# ---------------------------------------------------------------------------
# execute_edit
# ---------------------------------------------------------------------------

class TestExecuteEdit:
    def test_execute_existing_file(self, tmp_path):
        with patch.object(file_editor, "COMPANY_DIR", tmp_path), \
             patch.object(file_editor, "PROJECT_ROOT", tmp_path.parent), \
             patch("onemancompany.core.state.request_reload"):
            target = tmp_path / "test.md"
            target.write_text("original", encoding="utf-8")

            result = propose_edit("test.md", "updated", "fix", "00002")
            edit_id = result["edit_id"]

            exec_result = execute_edit(edit_id)
            assert exec_result["status"] == "applied"
            assert exec_result["backup_path"] is not None
            assert target.read_text() == "updated"

            # Backup should exist
            backup_dir = tmp_path / BACKUP_FOLDER_NAME
            assert backup_dir.exists()
            backups = list(backup_dir.iterdir())
            assert len(backups) == 1

    def test_execute_new_file(self, tmp_path):
        with patch.object(file_editor, "COMPANY_DIR", tmp_path), \
             patch.object(file_editor, "PROJECT_ROOT", tmp_path.parent), \
             patch("onemancompany.core.state.request_reload"):
            result = propose_edit("newdir/new.md", "hello", "create", "00002")
            edit_id = result["edit_id"]

            exec_result = execute_edit(edit_id)
            assert exec_result["status"] == "applied"
            assert exec_result["backup_path"] is None  # no original to backup
            assert (tmp_path / "newdir" / "new.md").read_text() == "hello"

    def test_execute_nonexistent_edit(self):
        result = execute_edit("nonexistent_id")
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_execute_removes_from_pending(self, tmp_path):
        with patch.object(file_editor, "COMPANY_DIR", tmp_path), \
             patch.object(file_editor, "PROJECT_ROOT", tmp_path.parent), \
             patch("onemancompany.core.state.request_reload"):
            target = tmp_path / "test.md"
            target.write_text("old", encoding="utf-8")

            result = propose_edit("test.md", "new", "fix", "00002")
            edit_id = result["edit_id"]
            assert edit_id in pending_file_edits

            execute_edit(edit_id)
            assert edit_id not in pending_file_edits


# ---------------------------------------------------------------------------
# reject_edit
# ---------------------------------------------------------------------------

class TestRejectEdit:
    def test_reject_existing_edit(self, tmp_path):
        with patch.object(file_editor, "COMPANY_DIR", tmp_path), \
             patch.object(file_editor, "PROJECT_ROOT", tmp_path.parent):
            target = tmp_path / "test.md"
            target.write_text("content", encoding="utf-8")

            result = propose_edit("test.md", "new", "reason", "00002")
            edit_id = result["edit_id"]

            rej = reject_edit(edit_id)
            assert rej["status"] == "rejected"
            assert edit_id not in pending_file_edits

    def test_reject_nonexistent_edit(self):
        result = reject_edit("nonexistent")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# list_pending_edits
# ---------------------------------------------------------------------------

class TestListPendingEdits:
    def test_empty_list(self):
        assert list_pending_edits() == []

    def test_lists_all_pending(self, tmp_path):
        with patch.object(file_editor, "COMPANY_DIR", tmp_path), \
             patch.object(file_editor, "PROJECT_ROOT", tmp_path.parent):
            (tmp_path / "a.md").write_text("a", encoding="utf-8")
            (tmp_path / "b.md").write_text("b", encoding="utf-8")

            propose_edit("a.md", "new_a", "reason_a", "00002")
            propose_edit("b.md", "new_b", "reason_b", "00003")

            edits = list_pending_edits()
            assert len(edits) == 2
            # Should contain summary fields, not full content
            for e in edits:
                assert "edit_id" in e
                assert "rel_path" in e
                assert "reason" in e
                assert "proposed_by" in e
                assert "old_content" not in e
                assert "new_content" not in e


# ---------------------------------------------------------------------------
# Edge cases: unreadable original file, relative path outside company dir
# ---------------------------------------------------------------------------

class TestProposeEditEdgeCases:
    def test_unable_to_read_original_file(self, tmp_path):
        """Line 77-78: When existing file can't be read, old_content = '(unable to read...)'."""
        with patch.object(file_editor, "COMPANY_DIR", tmp_path), \
             patch.object(file_editor, "PROJECT_ROOT", tmp_path.parent):
            target = tmp_path / "unreadable.md"
            target.write_text("original", encoding="utf-8")

            # Patch resolve to return a valid path but make read fail
            original_read_text = target.read_text
            call_count = 0

            def failing_read(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise PermissionError("cannot read file")
                return original_read_text(*args, **kwargs)

            with patch.object(type(target), "read_text", failing_read):
                result = propose_edit("unreadable.md", "new content", "fix", "00002")

            assert result["status"] == "pending_approval"
            edit_id = result["edit_id"]
            assert pending_file_edits[edit_id]["old_content"] == "(unable to read original file)"

    def test_relative_path_outside_company_dir_shows_absolute(self, tmp_path):
        """Lines 85-86: When resolved path not under COMPANY_DIR, rel_path is absolute."""
        from onemancompany.core.config import PROJECT_ROOT
        with patch.object(file_editor, "COMPANY_DIR", tmp_path / "company"), \
             patch.object(file_editor, "PROJECT_ROOT", tmp_path):
            # Set up with backend_code_maintenance permission for src/ access
            src_dir = tmp_path / "src" / "onemancompany"
            src_dir.mkdir(parents=True)
            target = src_dir / "test.py"
            target.write_text("code", encoding="utf-8")

            result = propose_edit(
                str(target), "new code", "fix", "00002",
                permissions=["backend_code_maintenance"],
            )
            # Since path is outside company dir, rel_path should be absolute
            assert result["status"] == "pending_approval"
            edit_id = result["edit_id"]
            assert pending_file_edits[edit_id]["rel_path"] == str(target.resolve())
