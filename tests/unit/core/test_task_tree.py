"""Tests for task tree data model and persistence."""
from __future__ import annotations

from onemancompany.core.task_tree import TaskNode, TaskTree


class TestTaskNode:
    def test_create_root_node(self):
        node = TaskNode(employee_id="00001", description="Root task")
        assert node.id  # auto-generated
        assert node.parent_id == ""
        assert node.children_ids == []
        assert node.status == "pending"
        assert node.acceptance_criteria == []
        assert node.created_at  # auto-set

    def test_create_child_node(self):
        node = TaskNode(
            employee_id="00010",
            description="Child task",
            parent_id="root123",
            acceptance_criteria=["Must pass tests"],
        )
        assert node.parent_id == "root123"
        assert node.acceptance_criteria == ["Must pass tests"]

    def test_to_dict_roundtrip(self):
        node = TaskNode(employee_id="00001", description="test")
        d = node.to_dict()
        restored = TaskNode.from_dict(d)
        assert restored.id == node.id
        assert restored.employee_id == node.employee_id
        assert restored.description == node.description


class TestTaskTree:
    def test_create_tree_with_root(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root task")
        assert tree.root_id == root.id
        assert tree.get_node(root.id) is root

    def test_add_child(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        child = tree.add_child(
            parent_id=root.id,
            employee_id="00010",
            description="Child task",
            acceptance_criteria=["Done correctly"],
        )
        assert child.parent_id == root.id
        assert child.id in root.children_ids
        assert child.acceptance_criteria == ["Done correctly"]

    def test_get_children(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "Task A", ["criterion A"])
        c2 = tree.add_child(root.id, "00011", "Task B", ["criterion B"])
        children = tree.get_children(root.id)
        assert len(children) == 2
        assert {c.id for c in children} == {c1.id, c2.id}

    def test_get_siblings(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c2 = tree.add_child(root.id, "00011", "B", [])
        siblings = tree.get_siblings(c1.id)
        assert len(siblings) == 1
        assert siblings[0].id == c2.id

    def test_all_siblings_terminal(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c2 = tree.add_child(root.id, "00011", "B", [])
        c1.status = "accepted"
        c2.status = "accepted"
        assert tree.all_children_terminal(root.id) is True

    def test_not_all_siblings_terminal(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c2 = tree.add_child(root.id, "00011", "B", [])
        c1.status = "accepted"
        c2.status = "processing"
        assert tree.all_children_terminal(root.id) is False

    def test_has_failed_children(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c2 = tree.add_child(root.id, "00011", "B", [])
        c1.status = "completed"
        c2.status = "failed"
        assert tree.has_failed_children(root.id) is True

    def test_save_and_load(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        child = tree.add_child(root.id, "00010", "Child", ["Must work"])
        child.status = "completed"
        child.result = "Done"

        path = tmp_path / "task_tree.yaml"
        tree.save(path)
        assert path.exists()

        loaded = TaskTree.load(path, project_id="proj1")
        assert loaded.root_id == root.id
        assert len(loaded.get_children(root.id)) == 1
        loaded_child = loaded.get_node(child.id)
        assert loaded_child.status == "completed"
        assert loaded_child.result == "Done"
        assert loaded_child.acceptance_criteria == ["Must work"]

    def test_task_node_default_timeout(self):
        node = TaskNode()
        assert node.timeout_seconds == 3600

    def test_task_node_custom_timeout(self):
        node = TaskNode(timeout_seconds=600)
        assert node.timeout_seconds == 600

    def test_timeout_in_to_dict(self):
        node = TaskNode(timeout_seconds=1800)
        d = node.to_dict()
        assert d["timeout_seconds"] == 1800

    def test_timeout_in_from_dict(self):
        node = TaskNode.from_dict({"timeout_seconds": 900})
        assert node.timeout_seconds == 900

    def test_add_child_with_timeout(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "Work", ["done"], timeout_seconds=1200)
        assert child.timeout_seconds == 1200

    def test_save_creates_parent_dirs(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        tree.create_root(employee_id="00001", description="Root")
        path = tmp_path / "deep" / "nested" / "task_tree.yaml"
        tree.save(path)
        assert path.exists()
