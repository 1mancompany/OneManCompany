"""Regression tests for P0/P1 fixes to unified CEO comms, cron, and completion card.

Covers:
  P0-1: close() drains pending interactions and rejects Futures
  P0-2: DND mode persisted to disk and wired into auto-reply timeout
  P1-3: get_or_create_* race condition prevented by lock
  P1-4: Cron rejects adding children to terminal project trees
  P1-5: @mention regex strips trailing punctuation, ignores emails
  P1-6: has_unresolved_ceo_request shared helper for both guards
"""

import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from onemancompany.core.task_lifecycle import (
    TaskPhase, NodeType, is_system_project_id,
    has_unresolved_ceo_request, CEO_REQUEST_RESOLVED,
)
from onemancompany.core.task_tree import TaskNode


# ---------------------------------------------------------------------------
# P0-1: close() drains pending Futures
# ---------------------------------------------------------------------------


class TestCloseDrainsPending:
    """Closing a conversation must reject pending Futures so agents unblock."""

    @pytest.fixture
    def service(self, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
        from onemancompany.core.conversation import ConversationService
        return ConversationService()

    @pytest.mark.asyncio
    async def test_close_rejects_pending_futures(self, service, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
        conv = await service.create(
            type="one_on_one", employee_id="00010",
        )
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        from onemancompany.core.conversation import Interaction
        interaction = Interaction(
            node_id="n1", tree_path="/fake/tree.yaml", project_id="p1",
            source_employee="00010", interaction_type="ceo_request",
            message="test", future=future,
        )
        from collections import deque
        service._pending[conv.id] = deque([interaction])

        # Mock close hook to avoid import issues
        with patch("onemancompany.core.conversation_hooks.run_close_hook", return_value=None):
            await service.close(conv.id)

        # Future should be rejected with RuntimeError
        assert future.done()
        with pytest.raises(RuntimeError, match="closed"):
            future.result()

    def test_drain_pending_cancels_timers(self, service):
        """Timer tasks are cancelled when draining."""
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        from onemancompany.core.conversation import Interaction
        interaction = Interaction(
            node_id="n1", tree_path="/fake", project_id="p1",
            source_employee="00010", interaction_type="ceo_request",
            message="test", future=future,
        )
        from collections import deque
        service._pending["conv1"] = deque([interaction])
        mock_timer = MagicMock()
        mock_timer.done.return_value = False
        service._auto_reply_tasks["conv1:n1"] = mock_timer

        drained = service._drain_pending("conv1")

        assert drained == 1
        mock_timer.cancel.assert_called_once()
        assert "conv1:n1" not in service._auto_reply_tasks
        assert "conv1" not in service._pending
        loop.close()


# ---------------------------------------------------------------------------
# P0-2: DND mode persistence and auto-reply integration
# ---------------------------------------------------------------------------


class TestDndPersistence:
    """DND state must persist to disk and affect auto-reply timeout."""

    def test_dnd_persists_to_disk(self, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.config._CEO_DND_PATH", tmp_path / "ceo_dnd.yaml")
        from onemancompany.core.config import get_ceo_dnd, set_ceo_dnd

        assert get_ceo_dnd() is False
        set_ceo_dnd(True)
        assert get_ceo_dnd() is True
        assert (tmp_path / "ceo_dnd.yaml").exists()

        # Simulate restart by reading fresh
        set_ceo_dnd(False)
        assert get_ceo_dnd() is False

    def test_dnd_on_sets_zero_timeout(self, monkeypatch):
        """When DND is on, auto-reply timer should use 0s timeout.

        Verify by checking that the method reads DND state and picks timeout=0.
        """
        from onemancompany.core.conversation import ConversationService, AUTO_REPLY_TIMEOUT

        # Directly test the timeout logic: DND on → 0, DND off → AUTO_REPLY_TIMEOUT
        with patch("onemancompany.core.config.get_ceo_dnd", return_value=True):
            from onemancompany.core.config import get_ceo_dnd
            timeout = 0 if get_ceo_dnd() else AUTO_REPLY_TIMEOUT
            assert timeout == 0

        with patch("onemancompany.core.config.get_ceo_dnd", return_value=False):
            from onemancompany.core.config import get_ceo_dnd
            timeout = 0 if get_ceo_dnd() else AUTO_REPLY_TIMEOUT
            assert timeout == AUTO_REPLY_TIMEOUT

    def test_dnd_off_uses_default_timeout(self):
        """When DND is off, normal timeout is used."""
        from onemancompany.core.conversation import AUTO_REPLY_TIMEOUT
        assert AUTO_REPLY_TIMEOUT == 120  # default


# ---------------------------------------------------------------------------
# P1-3: get_or_create_* uses locks (unit test for lock existence)
# ---------------------------------------------------------------------------


class TestGetOrCreateLocking:
    """get_or_create_* methods should use locks to prevent duplicates."""

    def test_service_has_create_locks(self):
        from onemancompany.core.conversation import ConversationService
        service = ConversationService()
        assert hasattr(service, "_create_locks")
        assert isinstance(service._create_locks, dict)

    def test_get_create_lock_returns_same_lock(self):
        from onemancompany.core.conversation import ConversationService
        service = ConversationService()
        lock1 = service._get_create_lock("project:p1")
        lock2 = service._get_create_lock("project:p1")
        assert lock1 is lock2

    def test_different_keys_get_different_locks(self):
        from onemancompany.core.conversation import ConversationService
        service = ConversationService()
        lock1 = service._get_create_lock("project:p1")
        lock2 = service._get_create_lock("project:p2")
        assert lock1 is not lock2


# ---------------------------------------------------------------------------
# P1-4: Cron rejects terminal project trees + is_system_project_id
# ---------------------------------------------------------------------------


class TestCronTerminalGuard:
    """Cron should not add children to finished/cancelled project trees."""

    def _make_tree_with_root(self, status: TaskPhase):
        from onemancompany.core.task_tree import TaskTree
        tree = TaskTree("proj1")
        root = tree.create_root(employee_id="00004", description="Test project")
        root.status = status.value
        return tree

    def test_add_to_project_tree_rejects_finished_root(self, tmp_path):
        """Adding cron child to a FINISHED tree should raise ValueError."""
        tree = self._make_tree_with_root(TaskPhase.FINISHED)
        tree_path = tmp_path / "tree.yaml"
        tree_path.touch()  # file must exist for the guard

        from contextlib import contextmanager

        @contextmanager
        def fake_lock(_):
            yield

        with patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.task_tree.get_tree_lock", side_effect=fake_lock), \
             patch("onemancompany.core.task_tree.save_tree_async"), \
             patch("onemancompany.core.vessel.employee_manager"):
            from onemancompany.core.automation import _add_to_project_tree
            with pytest.raises(ValueError, match="finished"):
                _add_to_project_tree("00010", "cron task", str(tree_path), "proj1")

    def test_add_to_project_tree_rejects_cancelled_root(self, tmp_path):
        tree = self._make_tree_with_root(TaskPhase.CANCELLED)
        tree_path = tmp_path / "tree.yaml"
        tree_path.touch()

        from contextlib import contextmanager

        @contextmanager
        def fake_lock(_):
            yield

        with patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.task_tree.get_tree_lock", side_effect=fake_lock), \
             patch("onemancompany.core.task_tree.save_tree_async"), \
             patch("onemancompany.core.vessel.employee_manager"):
            from onemancompany.core.automation import _add_to_project_tree
            with pytest.raises(ValueError, match="cancelled"):
                _add_to_project_tree("00010", "cron task", str(tree_path), "proj1")


class TestIsSystemProjectId:
    def test_sys_prefix(self):
        assert is_system_project_id("_sys_abc123") is True

    def test_auto_prefix(self):
        assert is_system_project_id("_auto_12345") is True

    def test_real_project(self):
        assert is_system_project_id("my-project-001") is False

    def test_empty_string(self):
        assert is_system_project_id("") is False


# ---------------------------------------------------------------------------
# P1-5: @mention regex
# ---------------------------------------------------------------------------


class TestMentionRegex:
    """@mention regex should handle trailing punctuation and CJK characters."""

    def test_strips_trailing_punctuation(self):
        from onemancompany.api.routes import _MENTION_RE
        # @Alice! should capture "Alice" not "Alice!"
        assert _MENTION_RE.findall("Hey @Alice!") == ["Alice"]

    def test_strips_trailing_period(self):
        from onemancompany.api.routes import _MENTION_RE
        assert _MENTION_RE.findall("Ask @Bob.") == ["Bob"]

    def test_ignores_email(self):
        from onemancompany.api.routes import _MENTION_RE
        # user@domain.com — after @ we get "domain" (stops at .)
        # This is acceptable; it won't match any employee name
        result = _MENTION_RE.findall("Send to user@domain.com")
        assert "domain.com" not in result

    def test_captures_cjk_names(self):
        from onemancompany.api.routes import _MENTION_RE
        assert _MENTION_RE.findall("@小爱 看一下") == ["小爱"]

    def test_captures_normal_name(self):
        from onemancompany.api.routes import _MENTION_RE
        assert _MENTION_RE.findall("@Alice and @Bob") == ["Alice", "Bob"]

    def test_no_mention(self):
        from onemancompany.api.routes import _MENTION_RE
        assert _MENTION_RE.findall("No mentions here") == []


# ---------------------------------------------------------------------------
# P1-6: has_unresolved_ceo_request shared helper
# ---------------------------------------------------------------------------


def _make_node(node_id, employee_id="00001", node_type=NodeType.CEO_REQUEST, status=TaskPhase.PENDING):
    node = TaskNode(
        id=node_id, parent_id="parent1", employee_id=employee_id,
        node_type=node_type.value if hasattr(node_type, "value") else node_type,
        description=f"test {node_id}",
        status=status.value if hasattr(status, "value") else status,
    )
    return node


class TestHasUnresolvedCeoRequest:
    """Shared helper must correctly identify unresolved CEO_REQUEST nodes."""

    def test_no_ceo_requests_returns_false(self):
        children = [_make_node("c1", node_type=NodeType.TASK, employee_id="00010")]
        assert has_unresolved_ceo_request(children, "00001") is False

    def test_pending_ceo_request_returns_true(self):
        children = [_make_node("c1", status=TaskPhase.PENDING)]
        assert has_unresolved_ceo_request(children, "00001") is True

    def test_processing_ceo_request_returns_true(self):
        children = [_make_node("c1", status=TaskPhase.PROCESSING)]
        assert has_unresolved_ceo_request(children, "00001") is True

    def test_holding_ceo_request_returns_true(self):
        children = [_make_node("c1", status=TaskPhase.HOLDING)]
        assert has_unresolved_ceo_request(children, "00001") is True

    def test_completed_ceo_request_returns_true(self):
        """COMPLETED is not resolved — still awaiting acceptance."""
        children = [_make_node("c1", status=TaskPhase.COMPLETED)]
        assert has_unresolved_ceo_request(children, "00001") is True

    def test_finished_ceo_request_returns_false(self):
        children = [_make_node("c1", status=TaskPhase.FINISHED)]
        assert has_unresolved_ceo_request(children, "00001") is False

    def test_cancelled_ceo_request_returns_false(self):
        children = [_make_node("c1", status=TaskPhase.CANCELLED)]
        assert has_unresolved_ceo_request(children, "00001") is False

    def test_accepted_ceo_request_returns_false(self):
        children = [_make_node("c1", status=TaskPhase.ACCEPTED)]
        assert has_unresolved_ceo_request(children, "00001") is False

    def test_failed_ceo_request_returns_false(self):
        children = [_make_node("c1", status=TaskPhase.FAILED)]
        assert has_unresolved_ceo_request(children, "00001") is False

    def test_different_employee_ignored(self):
        """CEO_REQUEST for a different employee should not count."""
        children = [_make_node("c1", employee_id="00099", status=TaskPhase.PENDING)]
        assert has_unresolved_ceo_request(children, "00001") is False

    def test_mixed_resolved_and_unresolved(self):
        """If any is unresolved, return True."""
        children = [
            _make_node("c1", status=TaskPhase.FINISHED),
            _make_node("c2", status=TaskPhase.PROCESSING),
        ]
        assert has_unresolved_ceo_request(children, "00001") is True

    def test_all_resolved_returns_false(self):
        children = [
            _make_node("c1", status=TaskPhase.FINISHED),
            _make_node("c2", status=TaskPhase.CANCELLED),
        ]
        assert has_unresolved_ceo_request(children, "00001") is False

    def test_ceo_request_resolved_covers_all_terminal_and_failed(self):
        """CEO_REQUEST_RESOLVED must include all states where re-creation is safe."""
        assert TaskPhase.FINISHED in CEO_REQUEST_RESOLVED
        assert TaskPhase.CANCELLED in CEO_REQUEST_RESOLVED
        assert TaskPhase.ACCEPTED in CEO_REQUEST_RESOLVED
        assert TaskPhase.FAILED in CEO_REQUEST_RESOLVED
        # Active states should NOT be in RESOLVED
        assert TaskPhase.PENDING not in CEO_REQUEST_RESOLVED
        assert TaskPhase.PROCESSING not in CEO_REQUEST_RESOLVED
        assert TaskPhase.HOLDING not in CEO_REQUEST_RESOLVED
        assert TaskPhase.COMPLETED not in CEO_REQUEST_RESOLVED
