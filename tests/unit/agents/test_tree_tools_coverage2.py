"""Coverage tests for agents/tree_tools.py — additional missing lines."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _add_to_project_team exception (lines 101-102)
# ---------------------------------------------------------------------------

class TestAddToProjectTeam:
    def test_write_failure_caught(self, tmp_path):
        from onemancompany.agents.tree_tools import _add_to_project_team
        # No project.yaml → should catch exception and log
        _add_to_project_team(str(tmp_path), "00010")  # should not raise


# ---------------------------------------------------------------------------
# _create_standalone_ceo_request (lines 144-149)
# ---------------------------------------------------------------------------

class TestCreateStandaloneCeoRequest:
    def test_creates_new_tree(self, tmp_path, monkeypatch):
        import onemancompany.agents.tree_tools as tt_mod
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "PROJECTS_DIR", tmp_path)

        mock_vessel = MagicMock()
        mock_vessel.employee_id = "00010"
        mock_manager = MagicMock()

        with patch("onemancompany.core.vessel.employee_manager", mock_manager), \
             patch("onemancompany.core.vessel._save_project_tree"), \
             patch("onemancompany.core.events.event_bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            result = tt_mod._create_standalone_ceo_request(
                "Please review this", "task_1", mock_vessel
            )
        assert result["status"] == "dispatched"


# ---------------------------------------------------------------------------
# accept_child — idempotent paths (lines 469, 471, 473, 477)
# ---------------------------------------------------------------------------

class TestAcceptChildIdempotent:
    def test_already_accepted(self):
        from onemancompany.agents.tree_tools import accept_child

        mock_node = MagicMock()
        mock_node.status = "accepted"
        mock_tree = MagicMock()
        mock_tree.get_node.return_value = mock_node

        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=("/tmp/proj", "/tmp/tree.yaml")), \
             patch("onemancompany.agents.tree_tools._load_tree", return_value=mock_tree), \
             patch("onemancompany.core.task_tree.get_tree_lock"):
            mock_cv.get.return_value = MagicMock()
            mock_tid.get.return_value = "parent_1"
            result = accept_child.invoke({"node_id": "child_1"})
        assert result.get("already_accepted") is True

    def test_already_cancelled(self):
        from onemancompany.agents.tree_tools import accept_child

        mock_node = MagicMock()
        mock_node.status = "cancelled"
        mock_tree = MagicMock()
        mock_tree.get_node.return_value = mock_node

        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=("/tmp/proj", "/tmp/tree.yaml")), \
             patch("onemancompany.agents.tree_tools._load_tree", return_value=mock_tree), \
             patch("onemancompany.core.task_tree.get_tree_lock"):
            mock_cv.get.return_value = MagicMock()
            mock_tid.get.return_value = "parent_1"
            result = accept_child.invoke({"node_id": "child_1"})
        assert result.get("already_cancelled") is True

    def test_wrong_status(self):
        from onemancompany.agents.tree_tools import accept_child

        mock_node = MagicMock()
        mock_node.status = "processing"
        mock_tree = MagicMock()
        mock_tree.get_node.return_value = mock_node

        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=("/tmp/proj", "/tmp/tree.yaml")), \
             patch("onemancompany.agents.tree_tools._load_tree", return_value=mock_tree), \
             patch("onemancompany.core.task_tree.get_tree_lock"):
            mock_cv.get.return_value = MagicMock()
            mock_tid.get.return_value = "parent_1"
            result = accept_child.invoke({"node_id": "child_1"})
        assert result["status"] == "error"
        assert "must be 'completed'" in result["message"]

    def test_node_not_found_with_children(self):
        """Cover lines 455-462: node not found, lists children as hint."""
        from onemancompany.agents.tree_tools import accept_child

        mock_child = MagicMock()
        mock_child.id = "real_child"
        mock_child.status = "completed"

        mock_tree = MagicMock()
        mock_tree.get_node.side_effect = lambda nid: None if nid == "bad_id" else MagicMock()
        mock_tree.get_children.return_value = [mock_child]

        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=("/tmp/proj", "/tmp/tree.yaml")), \
             patch("onemancompany.agents.tree_tools._load_tree", return_value=mock_tree), \
             patch("onemancompany.core.task_tree.get_tree_lock"):
            mock_cv.get.return_value = MagicMock()
            mock_tid.get.return_value = "parent_1"
            result = accept_child.invoke({"node_id": "bad_id"})
        assert "real_child" in result["message"]

    def test_no_context(self):
        from onemancompany.agents.tree_tools import accept_child
        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid:
            mock_cv.get.return_value = None
            mock_tid.get.return_value = None
            result = accept_child.invoke({"node_id": "x"})
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# reject_child (lines 507, 512, 519, 525)
# ---------------------------------------------------------------------------

class TestRejectChild:
    def test_no_project_context(self):
        from onemancompany.agents.tree_tools import reject_child
        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=("", "")):
            mock_cv.get.return_value = MagicMock()
            mock_tid.get.return_value = "tid"
            result = reject_child.invoke({"node_id": "x", "reason": "bad"})
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# unblock_child (lines 585, 590, 597, 619)
# ---------------------------------------------------------------------------

class TestUnblockChild:
    def test_no_context(self):
        from onemancompany.agents.tree_tools import unblock_child
        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid:
            mock_cv.get.return_value = None
            mock_tid.get.return_value = None
            result = unblock_child.invoke({"node_id": "x"})
        assert result["status"] == "error"

    def test_no_project(self):
        from onemancompany.agents.tree_tools import unblock_child
        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=("", "")):
            mock_cv.get.return_value = MagicMock()
            mock_tid.get.return_value = "tid"
            result = unblock_child.invoke({"node_id": "x"})
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# cancel_child (lines 644, 649, 656)
# ---------------------------------------------------------------------------

class TestCancelChild:
    def test_no_context(self):
        from onemancompany.agents.tree_tools import cancel_child
        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid:
            mock_cv.get.return_value = None
            mock_tid.get.return_value = None
            result = cancel_child.invoke({"node_id": "x"})
        assert result["status"] == "error"

    def test_no_project(self):
        from onemancompany.agents.tree_tools import cancel_child
        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=("", "")):
            mock_cv.get.return_value = MagicMock()
            mock_tid.get.return_value = "tid"
            result = cancel_child.invoke({"node_id": "x"})
        assert result["status"] == "error"

    def test_node_not_found(self):
        from onemancompany.agents.tree_tools import cancel_child
        mock_tree = MagicMock()
        mock_tree.get_node.return_value = None
        with patch("onemancompany.core.vessel._current_vessel") as mock_cv, \
             patch("onemancompany.core.vessel._current_task_id") as mock_tid, \
             patch("onemancompany.agents.tree_tools._find_entry_for_task", return_value=("/tmp/proj", "/tmp/tree.yaml")), \
             patch("onemancompany.agents.tree_tools._load_tree", return_value=mock_tree), \
             patch("onemancompany.core.task_tree.get_tree_lock"):
            mock_cv.get.return_value = MagicMock()
            mock_tid.get.return_value = "tid"
            result = cancel_child.invoke({"node_id": "bad"})
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# create_project (lines 719-808)
# ---------------------------------------------------------------------------

class TestCreateProjectFromChat:
    def test_empty_task(self):
        from onemancompany.agents.tree_tools import create_project
        result = create_project.invoke({"task": ""})
        assert result["status"] == "error"

    def test_invalid_mode_defaults_standard(self):
        from onemancompany.agents.tree_tools import create_project
        mock_manager = MagicMock()
        with patch("onemancompany.core.project_archive.async_create_project_from_task",
                    return_value=("pid1", "iter1")), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"), \
             patch("onemancompany.core.vessel._save_project_tree"), \
             patch("onemancompany.core.vessel.employee_manager", mock_manager), \
             patch("onemancompany.core.conversation.get_conversation_service") as mock_conv:
            mock_conv.return_value.get_or_create_project_conversation = AsyncMock()
            result = create_project.invoke({"task": "Do something", "mode": "bogus"})
        assert result["status"] == "ok"

    def test_exception_caught(self):
        from onemancompany.agents.tree_tools import create_project
        with patch("onemancompany.core.project_archive.async_create_project_from_task",
                    side_effect=RuntimeError("boom")):
            result = create_project.invoke({"task": "Do something"})
        assert result["status"] == "error"
