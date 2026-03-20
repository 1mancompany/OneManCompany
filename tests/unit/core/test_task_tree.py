"""Tests for task tree data model and persistence."""
from __future__ import annotations

import pytest

from onemancompany.core.task_lifecycle import TaskPhase, TaskTransitionError, VALID_TRANSITIONS
from onemancompany.core.task_tree import TaskNode, TaskTree


class TestDependencyTransitions:
    def test_pending_to_blocked_allowed(self):
        assert TaskPhase.BLOCKED in VALID_TRANSITIONS[TaskPhase.PENDING]

    def test_blocked_to_pending_allowed(self):
        assert TaskPhase.PENDING in VALID_TRANSITIONS[TaskPhase.BLOCKED]


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
        # Skeleton roundtrip: description excluded, preview preserved
        assert restored.description_preview == node.description_preview


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
        assert tree.all_children_done(root.id) is True

    def test_not_all_siblings_terminal(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00001", description="Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c2 = tree.add_child(root.id, "00011", "B", [])
        c1.status = "accepted"
        c2.status = "processing"
        assert tree.all_children_done(root.id) is False

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
        # Content is lazy-loaded; load it explicitly
        loaded_child.load_content(tmp_path)
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


class TestTaskNodeBranch:
    def test_default_branch_values(self):
        node = TaskNode(employee_id="00001", description="test")
        assert node.branch == 0
        assert node.branch_active is True

    def test_branch_in_to_dict(self):
        node = TaskNode(employee_id="00001", description="test", branch=2, branch_active=False)
        d = node.to_dict()
        assert d["branch"] == 2
        assert d["branch_active"] is False

    def test_branch_in_from_dict(self):
        node = TaskNode.from_dict({"branch": 3, "branch_active": False})
        assert node.branch == 3
        assert node.branch_active is False

    def test_from_dict_missing_branch_defaults(self):
        """Backward compat: old YAML without branch fields."""
        node = TaskNode.from_dict({"employee_id": "00001"})
        assert node.branch == 0
        assert node.branch_active is True


class TestTaskNodeDependency:
    def test_default_depends_on_empty(self):
        node = TaskNode()
        assert node.depends_on == []

    def test_depends_on_set(self):
        node = TaskNode(depends_on=["abc", "def"])
        assert node.depends_on == ["abc", "def"]

    def test_to_dict_includes_depends_on(self):
        node = TaskNode(depends_on=["abc"])
        d = node.to_dict()
        assert d["depends_on"] == ["abc"]

    def test_from_dict_loads_depends_on(self):
        d = {"id": "x", "depends_on": ["a", "b"], "fail_strategy": "continue"}
        node = TaskNode.from_dict(d)
        assert node.depends_on == ["a", "b"]

    def test_from_dict_without_depends_on_defaults(self):
        """Backward compat: old YAML without depends_on loads fine."""
        d = {"id": "x", "status": "pending"}
        node = TaskNode.from_dict(d)
        assert node.depends_on == []


class TestTaskTreeBranching:
    def test_initial_branch(self):
        tree = TaskTree(project_id="proj1")
        assert tree.current_branch == 0

    def test_new_branch_increments(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "accepted"
        new_b = tree.new_branch()
        assert new_b == 1
        assert tree.current_branch == 1

    def test_new_branch_deactivates_old_nodes(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "accepted"
        tree.new_branch()
        assert c1.branch_active is False
        assert root.branch_active is True

    def test_all_children_terminal_filters_active_branch(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "accepted"
        tree.new_branch()
        c2 = tree.add_child(root.id, "00011", "B", [])
        c2.branch = tree.current_branch
        c2.branch_active = True
        assert tree.all_children_done(root.id) is False
        c2.status = "accepted"
        assert tree.all_children_done(root.id) is True

    def test_get_active_children(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "accepted"
        tree.new_branch()
        c2 = tree.add_child(root.id, "00011", "B", [])
        c2.branch = tree.current_branch
        c2.branch_active = True
        active = tree.get_active_children(root.id)
        assert len(active) == 1
        assert active[0].id == c2.id

    def test_has_failed_children_filters_active_branch(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "failed"
        tree.new_branch()
        c2 = tree.add_child(root.id, "00011", "B", [])
        c2.branch = tree.current_branch
        c2.branch_active = True
        # c1 is failed but inactive — should return False
        assert tree.has_failed_children(root.id) is False
        c2.status = "failed"
        assert tree.has_failed_children(root.id) is True

    def test_branch_persists_in_save_load(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "accepted"
        tree.new_branch()
        c2 = tree.add_child(root.id, "00011", "B", [])
        c2.branch = tree.current_branch
        c2.branch_active = True
        path = tmp_path / "task_tree.yaml"
        tree.save(path)
        loaded = TaskTree.load(path)
        assert loaded.current_branch == 1
        loaded_c1 = loaded.get_node(c1.id)
        assert loaded_c1.branch_active is False
        loaded_c2 = loaded.get_node(c2.id)
        assert loaded_c2.branch_active is True
        assert loaded_c2.branch == 1


class TestTaskTreeDependencyHelpers:
    def test_add_child_with_depends_on(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        assert b.depends_on == [a.id]

    def test_find_dependents(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        c = tree.add_child(root.id, "e3", "task C", [], depends_on=[a.id])
        d = tree.add_child(root.id, "e4", "task D", [])
        dependents = tree.find_dependents(a.id)
        dep_ids = {n.id for n in dependents}
        assert dep_ids == {b.id, c.id}

    def test_find_dependents_empty(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        assert tree.find_dependents(a.id) == []

    def test_all_deps_resolved_true(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "accepted"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        assert tree.all_deps_resolved(b.id) is True

    def test_all_deps_resolved_false(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        assert tree.all_deps_resolved(b.id) is False

    def test_has_failed_deps(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "failed"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        assert tree.has_failed_deps(b.id) is True

    def test_save_load_preserves_depends_on(self, tmp_path):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        path = tmp_path / "tree.yaml"
        tree.save(path)
        loaded = TaskTree.load(path)
        lb = loaded.get_node(b.id)
        assert lb.depends_on == [a.id]


class TestTaskNodeSSoT:
    def test_node_set_status_valid(self):
        node = TaskNode(employee_id="e1", description="test")
        assert node.status == "pending"
        node.set_status(TaskPhase.PROCESSING)
        assert node.status == "processing"

    def test_node_set_status_invalid(self):
        node = TaskNode(employee_id="e1", description="test")
        with pytest.raises(TaskTransitionError):
            node.set_status(TaskPhase.ACCEPTED)  # can't go pending → accepted

    def test_node_is_resolved(self):
        node = TaskNode(employee_id="e1", description="test", status="accepted")
        assert node.is_resolved is True
        node2 = TaskNode(employee_id="e1", description="test", status="completed")
        assert node2.is_resolved is False

    def test_node_is_done_executing(self):
        node = TaskNode(employee_id="e1", description="test", status="completed")
        assert node.is_done_executing is True
        node2 = TaskNode(employee_id="e1", description="test", status="processing")
        assert node2.is_done_executing is False

    def test_node_unblocks_dependents(self):
        node = TaskNode(employee_id="e1", description="test", status="accepted")
        assert node.unblocks_dependents is True
        node2 = TaskNode(employee_id="e1", description="test", status="failed")
        assert node2.unblocks_dependents is False

    def test_node_new_fields_in_dict(self):
        node = TaskNode(employee_id="e1", description="test", model_used="gpt-4", project_dir="/tmp/proj")
        d = node.to_dict()
        assert d["model_used"] == "gpt-4"
        assert d["project_dir"] == "/tmp/proj"

    def test_node_from_dict_new_fields(self):
        d = {"employee_id": "e1", "description": "test", "task_type": "project", "model_used": "gpt-4", "project_dir": "/p"}
        node = TaskNode.from_dict(d)
        assert node.model_used == "gpt-4"

    def test_tree_all_children_done(self):
        tree = TaskTree(project_id="p1")
        root = tree.create_root("e1", "root")
        c1 = tree.add_child(root.id, "e2", "child1", [])
        c2 = tree.add_child(root.id, "e3", "child2", [])
        c1.status = "completed"
        c2.status = "accepted"
        assert tree.all_children_done(root.id) is True

    def test_tree_all_children_done_false_when_processing(self):
        tree = TaskTree(project_id="p1")
        root = tree.create_root("e1", "root")
        c1 = tree.add_child(root.id, "e2", "child1", [])
        c1.status = "processing"
        assert tree.all_children_done(root.id) is False

    def test_tree_all_deps_resolved(self):
        tree = TaskTree(project_id="p1")
        root = tree.create_root("e1", "root")
        c1 = tree.add_child(root.id, "e2", "child1", [])
        c2 = tree.add_child(root.id, "e3", "child2", [], depends_on=[c1.id])
        c1.status = "accepted"
        assert tree.all_deps_resolved(c2.id) is True

    def test_status_migration_complete_to_completed(self):
        """Old 'complete' status should be normalized to 'completed' on load."""
        d = {"employee_id": "e1", "description": "test", "status": "complete"}
        node = TaskNode.from_dict(d)
        assert node.status == "completed"


class TestTaskNodeContentExternalization:
    """Tests for lazy-loaded description/result with dirty tracking."""

    def test_description_setter_marks_dirty(self):
        node = TaskNode(employee_id="e1")
        node.description = "hello"
        assert node.description == "hello"
        assert node._content_dirty is True

    def test_result_setter_marks_dirty(self):
        node = TaskNode(employee_id="e1")
        node.result = "done"
        assert node.result == "done"
        assert node._content_dirty is True

    def test_description_preview_truncated(self):
        node = TaskNode(employee_id="e1")
        node.description = "A" * 500
        assert node.description_preview == "A" * 200

    def test_description_preview_short_text(self):
        node = TaskNode(employee_id="e1")
        node.description = "short"
        assert node.description_preview == "short"

    def test_save_content_creates_file(self, tmp_path):
        node = TaskNode(employee_id="e1")
        node.description = "task desc"
        node.result = "task result"
        node.save_content(tmp_path)
        content_path = tmp_path / "nodes" / f"{node.id}.yaml"
        assert content_path.exists()
        import yaml
        data = yaml.safe_load(content_path.read_text())
        assert data["description"] == "task desc"
        assert data["result"] == "task result"

    def test_save_content_skips_when_not_dirty(self, tmp_path):
        node = TaskNode(employee_id="e1")
        node._content_dirty = False
        node.save_content(tmp_path)
        content_path = tmp_path / "nodes" / f"{node.id}.yaml"
        assert not content_path.exists()

    def test_save_content_resets_dirty_flag(self, tmp_path):
        node = TaskNode(employee_id="e1")
        node.description = "x"
        node.save_content(tmp_path)
        assert node._content_dirty is False

    def test_load_content_reads_file(self, tmp_path):
        node = TaskNode(employee_id="e1", id="test123")
        node.description = "original"
        node.result = "original result"
        node.save_content(tmp_path)
        # Reset fields
        object.__setattr__(node, "description", "")
        object.__setattr__(node, "result", "")
        node._content_loaded = False
        node._content_dirty = False
        node.load_content(tmp_path)
        assert node.description == "original"
        assert node.result == "original result"
        assert node._content_loaded is True

    def test_load_content_idempotent(self, tmp_path):
        node = TaskNode(employee_id="e1", id="test123")
        node.description = "original"
        node.save_content(tmp_path)
        node._content_loaded = False
        node.load_content(tmp_path)
        # Modify in-memory (use object.__setattr__ to avoid dirty tracking for test)
        object.__setattr__(node, "description", "modified")
        # Second load should NOT overwrite
        node.load_content(tmp_path)
        assert node.description == "modified"

    def test_load_content_missing_file_is_noop(self, tmp_path):
        node = TaskNode(employee_id="e1", id="missing123")
        node.load_content(tmp_path)
        assert node._content_loaded is True
        assert node.description == ""

    def test_to_dict_excludes_description_and_result(self):
        node = TaskNode(employee_id="e1")
        node.description = "big text"
        node.result = "big result"
        d = node.to_dict()
        assert "description" not in d
        assert "result" not in d
        assert d["description_preview"] == "big text"

    def test_from_dict_with_old_format_migrates(self):
        """Backward compat: old YAML with inline description/result."""
        d = {
            "id": "old123",
            "employee_id": "e1",
            "description": "legacy desc",
            "result": "legacy result",
            "status": "completed",
        }
        node = TaskNode.from_dict(d)
        assert node.description == "legacy desc"
        assert node.result == "legacy result"
        assert node._content_dirty is True
        assert node._content_loaded is True

    def test_from_dict_without_description_result(self):
        """New format: no description/result in skeleton dict."""
        d = {
            "id": "new123",
            "employee_id": "e1",
            "description_preview": "preview text",
            "status": "pending",
        }
        node = TaskNode.from_dict(d)
        assert node.description == ""
        assert node.result == ""
        assert node._content_dirty is False
        assert node.description_preview == "preview text"

    def test_constructor_sets_dirty_for_nonempty_description(self):
        """Nodes created with description via constructor should be dirty."""
        node = TaskNode(employee_id="e1", description="new task")
        assert node._content_dirty is True
        assert node.description_preview == "new task"


class TestSubtreeResolved:
    """Tests for is_subtree_resolved() and is_project_complete()."""

    def _make_tree(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("ceo", "CEO prompt")
        root.node_type = "ceo_prompt"
        ea = tree.add_child(root.id, "ea", "EA task", [])
        ea.node_type = "task"
        return tree, root, ea

    def test_leaf_resolved(self):
        tree, root, ea = self._make_tree()
        child = tree.add_child(ea.id, "e1", "leaf", [])
        child.status = "accepted"
        assert tree.is_subtree_resolved(child.id) is True

    def test_leaf_not_resolved(self):
        tree, root, ea = self._make_tree()
        child = tree.add_child(ea.id, "e1", "leaf", [])
        child.status = "completed"
        assert tree.is_subtree_resolved(child.id) is False

    def test_subtree_with_unresolved_descendant(self):
        tree, root, ea = self._make_tree()
        mid = tree.add_child(ea.id, "e1", "mid", [])
        mid.status = "accepted"
        leaf = tree.add_child(mid.id, "e2", "leaf", [])
        leaf.status = "processing"
        assert tree.is_subtree_resolved(mid.id) is False

    def test_subtree_fully_resolved(self):
        tree, root, ea = self._make_tree()
        mid = tree.add_child(ea.id, "e1", "mid", [])
        mid.status = "accepted"
        leaf = tree.add_child(mid.id, "e2", "leaf", [])
        leaf.status = "finished"
        assert tree.is_subtree_resolved(mid.id) is True

    def test_project_complete_all_resolved(self):
        tree, root, ea = self._make_tree()
        ea.status = "completed"  # done executing
        c1 = tree.add_child(ea.id, "e1", "c1", [])
        c1.status = "accepted"
        c2 = tree.add_child(ea.id, "e2", "c2", [])
        c2.status = "finished"
        assert tree.is_project_complete() is True

    def test_project_not_complete_child_pending(self):
        tree, root, ea = self._make_tree()
        ea.status = "completed"
        c1 = tree.add_child(ea.id, "e1", "c1", [])
        c1.status = "accepted"
        c2 = tree.add_child(ea.id, "e2", "c2", [])
        c2.status = "pending"
        assert tree.is_project_complete() is False

    def test_project_not_complete_ea_still_processing(self):
        tree, root, ea = self._make_tree()
        ea.status = "processing"
        c1 = tree.add_child(ea.id, "e1", "c1", [])
        c1.status = "accepted"
        assert tree.is_project_complete() is False

    def test_project_complete_with_failed_child(self):
        """Failed children are RESOLVED — project should still complete."""
        tree, root, ea = self._make_tree()
        ea.status = "completed"
        c1 = tree.add_child(ea.id, "e1", "c1", [])
        c1.status = "accepted"
        c2 = tree.add_child(ea.id, "e2", "c2", [])
        c2.status = "failed"
        assert tree.is_project_complete() is True

    def test_project_complete_deep_tree(self):
        """Deep tree: all descendants must be resolved."""
        tree, root, ea = self._make_tree()
        ea.status = "completed"
        mid = tree.add_child(ea.id, "e1", "mid", [])
        mid.status = "accepted"
        leaf = tree.add_child(mid.id, "e2", "leaf", [])
        leaf.status = "accepted"
        assert tree.is_project_complete() is True

    def test_project_not_complete_deep_unresolved(self):
        """Deep tree: one unresolved leaf blocks completion."""
        tree, root, ea = self._make_tree()
        ea.status = "completed"
        mid = tree.add_child(ea.id, "e1", "mid", [])
        mid.status = "accepted"
        leaf = tree.add_child(mid.id, "e2", "leaf", [])
        leaf.status = "completed"  # not yet accepted
        assert tree.is_project_complete() is False

    def test_legacy_tree_root_is_ea(self):
        """Legacy tree where root is the EA (no CEO prompt node)."""
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("ea", "EA is root")
        root.node_type = "task"
        root.status = "completed"
        child = tree.add_child(root.id, "e1", "child", [])
        child.status = "accepted"
        assert tree.is_project_complete() is True


class TestTaskTreeContentExternalization:
    def test_save_creates_node_content_files(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("e1", "Root description")
        child = tree.add_child(root.id, "e2", "Child desc", ["criterion"])
        child.result = "Child result"

        path = tmp_path / "task_tree.yaml"
        tree.save(path)

        # Skeleton should NOT contain description/result
        import yaml
        skeleton = yaml.safe_load(path.read_text())
        for nd in skeleton["nodes"]:
            assert "description" not in nd
            assert "result" not in nd
            assert "description_preview" in nd

        # Content files should exist
        assert (tmp_path / "nodes" / f"{root.id}.yaml").exists()
        assert (tmp_path / "nodes" / f"{child.id}.yaml").exists()

    def test_load_skeleton_only(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("e1", "Root description with lots of text")
        root.result = "Root result"
        path = tmp_path / "task_tree.yaml"
        tree.save(path)

        loaded = TaskTree.load(path, skeleton_only=True)
        loaded_root = loaded.get_node(root.id)
        # Description/result should be empty (not loaded yet)
        assert loaded_root.description == ""
        assert loaded_root.result == ""
        # Preview should be available
        assert loaded_root.description_preview == "Root description with lots of text"

    def test_load_then_load_content(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("e1", "Full description")
        root.result = "Full result"
        path = tmp_path / "task_tree.yaml"
        tree.save(path)

        loaded = TaskTree.load(path)
        loaded_root = loaded.get_node(root.id)
        loaded_root.load_content(tmp_path)
        assert loaded_root.description == "Full description"
        assert loaded_root.result == "Full result"

    def test_backward_compat_old_format(self, tmp_path):
        """Load a tree saved in old format (description/result inline)."""
        import yaml
        old_data = {
            "project_id": "proj1",
            "root_id": "old_root",
            "current_branch": 0,
            "nodes": [{
                "id": "old_root",
                "employee_id": "e1",
                "description": "Legacy inline description",
                "result": "Legacy inline result",
                "status": "completed",
                "parent_id": "",
                "children_ids": [],
                "acceptance_criteria": [],
                "node_type": "task",
                "task_type": "simple",
                "model_used": "",
                "project_dir": "",
                "acceptance_result": None,
                "project_id": "proj1",
                "created_at": "2026-01-01",
                "completed_at": "",
                "cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "timeout_seconds": 3600,
                "branch": 0,
                "branch_active": True,
                "depends_on": [],
                "fail_strategy": "block",
            }],
        }
        path = tmp_path / "task_tree.yaml"
        path.write_text(yaml.dump(old_data, allow_unicode=True), encoding="utf-8")

        loaded = TaskTree.load(path)
        root = loaded.get_node("old_root")
        # Old format: description/result loaded inline, marked dirty
        assert root.description == "Legacy inline description"
        assert root.result == "Legacy inline result"
        assert root._content_dirty is True

        # Save should migrate to new format
        loaded.save(path)
        skeleton = yaml.safe_load(path.read_text())
        for nd in skeleton["nodes"]:
            assert "description" not in nd
            assert "result" not in nd
        assert (tmp_path / "nodes" / "old_root.yaml").exists()


class TestTaskTreeMode:
    def test_default_mode_is_standard(self):
        tree = TaskTree(project_id="p1")
        assert tree.mode == "standard"

    def test_mode_set_on_init(self):
        tree = TaskTree(project_id="p1", mode="simple")
        assert tree.mode == "simple"

    def test_mode_persisted_in_save(self, tmp_path):
        tree = TaskTree(project_id="p1", mode="simple")
        root = tree.create_root(employee_id="00001", description="test")
        tree.save(tmp_path / "tree.yaml")
        loaded = TaskTree.load(tmp_path / "tree.yaml")
        assert loaded.mode == "simple"

    def test_load_without_mode_defaults_to_standard(self, tmp_path):
        """Backward compat: old trees without mode field default to standard."""
        import yaml
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="00001", description="test")
        tree.save(tmp_path / "tree.yaml")
        # Strip mode from YAML to simulate old file
        data = yaml.safe_load((tmp_path / "tree.yaml").read_text())
        data.pop("mode", None)
        (tmp_path / "tree.yaml").write_text(yaml.dump(data, allow_unicode=True))
        loaded = TaskTree.load(tmp_path / "tree.yaml")
        assert loaded.mode == "standard"
