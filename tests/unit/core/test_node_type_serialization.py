"""Tests for TaskNode node_type serialization/deserialization.

Covers the bug where node_type was serialized as 'NodeType.CEO_REQUEST'
(enum repr) instead of 'ceo_request' (enum value), causing comparisons
to fail after deserialization.
"""

from __future__ import annotations

from onemancompany.core.task_lifecycle import NodeType
from onemancompany.core.task_tree import TaskNode


class TestNodeTypeSerialization:
    """Ensure node_type round-trips correctly through to_dict/from_dict."""

    def test_to_dict_with_enum_member(self):
        """to_dict should serialize enum member as its value string."""
        node = TaskNode(id="n1", node_type=NodeType.CEO_REQUEST)
        d = node.to_dict()
        assert d["node_type"] == "ceo_request"

    def test_to_dict_with_string_value(self):
        """to_dict should pass through plain strings unchanged."""
        node = TaskNode(id="n1", node_type="task")
        d = node.to_dict()
        assert d["node_type"] == "task"

    def test_from_dict_migrates_old_enum_repr(self):
        """from_dict should migrate 'NodeType.CEO_REQUEST' → 'ceo_request'."""
        d = {
            "id": "n1",
            "node_type": "NodeType.CEO_REQUEST",
            "description_preview": "test",
        }
        node = TaskNode.from_dict(d)
        assert node.node_type == "ceo_request"
        assert node.node_type == NodeType.CEO_REQUEST

    def test_from_dict_migrates_watchdog_nudge(self):
        d = {
            "id": "n1",
            "node_type": "NodeType.WATCHDOG_NUDGE",
            "description_preview": "test",
        }
        node = TaskNode.from_dict(d)
        assert node.node_type == "watchdog_nudge"
        assert node.node_type == NodeType.WATCHDOG_NUDGE

    def test_from_dict_normal_value_unchanged(self):
        """Normal value strings should not be affected by migration."""
        d = {
            "id": "n1",
            "node_type": "task",
            "description_preview": "test",
        }
        node = TaskNode.from_dict(d)
        assert node.node_type == "task"

    def test_roundtrip_preserves_value(self):
        """Serialize → deserialize should preserve node_type correctly."""
        node = TaskNode(id="n1", node_type=NodeType.CEO_PROMPT)
        d = node.to_dict()
        node2 = TaskNode.from_dict(d)
        assert node2.node_type == NodeType.CEO_PROMPT
        assert d["node_type"] == "ceo_prompt"
