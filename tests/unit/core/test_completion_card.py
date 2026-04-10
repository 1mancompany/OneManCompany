"""Regression test: completion card should be re-creatable after CEO responds.

Bug: existing_confirm guard blocked ALL CEO_REQUEST children, even already-resolved
ones. After CEO replied to a completion card and the project got new work, the second
completion never triggered because the old (finished) CEO_REQUEST still existed.

Fix: Only block on unresolved (pending/processing) CEO_REQUEST nodes.
Now uses shared has_unresolved_ceo_request() from task_lifecycle.
"""

from onemancompany.core.task_lifecycle import TaskPhase, NodeType, has_unresolved_ceo_request
from onemancompany.core.task_tree import TaskNode


def _make_node(node_id, parent_id="", employee_id="00004", node_type=NodeType.TASK, status=TaskPhase.FINISHED.value):
    node = TaskNode(
        id=node_id,
        parent_id=parent_id,
        employee_id=employee_id,
        node_type=node_type.value if hasattr(node_type, 'value') else node_type,
        description=f"test node {node_id}",
        status=status,
    )
    return node


class TestCompletionCardGuard:
    """The existing_confirm guard should only block on unresolved CEO_REQUEST nodes."""

    def test_no_existing_confirm_allows_creation(self):
        """No CEO_REQUEST children → should allow creation."""
        children = [
            _make_node("child1", parent_id="ea1", employee_id="00006"),
        ]
        assert has_unresolved_ceo_request(children, "00001") is False

    def test_finished_confirm_allows_re_creation(self):
        """A FINISHED CEO_REQUEST should NOT block a new completion card."""
        children = [
            _make_node("confirm1", parent_id="ea1", employee_id="00001",
                       node_type=NodeType.CEO_REQUEST, status=TaskPhase.FINISHED.value),
        ]
        assert has_unresolved_ceo_request(children, "00001") is False

    def test_pending_confirm_blocks_duplicate(self):
        """A PENDING CEO_REQUEST should block duplicate creation."""
        children = [
            _make_node("confirm1", parent_id="ea1", employee_id="00001",
                       node_type=NodeType.CEO_REQUEST, status=TaskPhase.PENDING.value),
        ]
        assert has_unresolved_ceo_request(children, "00001") is True

    def test_cancelled_confirm_allows_re_creation(self):
        """A CANCELLED CEO_REQUEST should NOT block a new completion card."""
        children = [
            _make_node("confirm1", parent_id="ea1", employee_id="00001",
                       node_type=NodeType.CEO_REQUEST, status=TaskPhase.CANCELLED.value),
        ]
        assert has_unresolved_ceo_request(children, "00001") is False

    def test_processing_confirm_blocks_duplicate(self):
        """A PROCESSING CEO_REQUEST should block duplicate creation."""
        children = [
            _make_node("confirm1", parent_id="ea1", employee_id="00001",
                       node_type=NodeType.CEO_REQUEST, status=TaskPhase.PROCESSING.value),
        ]
        assert has_unresolved_ceo_request(children, "00001") is True
