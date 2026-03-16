"""Tests for SystemTaskTree."""
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from onemancompany.core.system_tasks import SystemTaskTree
from onemancompany.core.task_lifecycle import TaskPhase


def test_create_system_node():
    tree = SystemTaskTree("emp1")
    node = tree.create_system_node("emp1", "[cron:daily] Run report")
    assert node.node_type == "system"
    assert node.employee_id == "emp1"
    assert node.status == "pending"


def test_save_and_load(tmp_path):
    tree = SystemTaskTree("emp1")
    tree.create_system_node("emp1", "task1")
    path = tmp_path / "system_tasks.yaml"
    tree.save(path)
    loaded = SystemTaskTree.load(path, "emp1")
    assert len(loaded.get_all_nodes()) == 1


def test_auto_cleanup_old_finished(tmp_path):
    tree = SystemTaskTree("emp1")
    node = tree.create_system_node("emp1", "old task")
    node.status = TaskPhase.FINISHED.value
    node.completed_at = (datetime.now() - timedelta(hours=25)).isoformat()
    path = tmp_path / "system_tasks.yaml"
    tree.save(path)  # should auto-clean
    loaded = SystemTaskTree.load(path, "emp1")
    assert len(loaded.get_all_nodes()) == 0


def test_keeps_recent_finished(tmp_path):
    tree = SystemTaskTree("emp1")
    node = tree.create_system_node("emp1", "recent task")
    node.status = TaskPhase.FINISHED.value
    node.completed_at = datetime.now().isoformat()
    path = tmp_path / "system_tasks.yaml"
    tree.save(path)
    loaded = SystemTaskTree.load(path, "emp1")
    assert len(loaded.get_all_nodes()) == 1
