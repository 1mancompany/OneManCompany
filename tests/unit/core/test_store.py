"""Tests for core/store.py — unified read/write layer."""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path


def test_read_yaml_returns_dict(tmp_path):
    """_read_yaml reads a YAML file and returns its contents."""
    p = tmp_path / "test.yaml"
    p.write_text("name: Alice\nage: 30\n")
    from onemancompany.core.store import _read_yaml
    result = _read_yaml(p)
    assert result == {"name": "Alice", "age": 30}


def test_read_yaml_missing_returns_empty(tmp_path):
    """_read_yaml returns {} for missing files."""
    from onemancompany.core.store import _read_yaml
    result = _read_yaml(tmp_path / "missing.yaml")
    assert result == {}


def test_write_yaml_creates_file(tmp_path):
    """_write_yaml writes data to a YAML file."""
    p = tmp_path / "out.yaml"
    from onemancompany.core.store import _write_yaml
    _write_yaml(p, {"name": "Bob", "skills": ["python"]})
    loaded = yaml.safe_load(p.read_text())
    assert loaded["name"] == "Bob"
    assert loaded["skills"] == ["python"]


import asyncio

@pytest.mark.asyncio
async def test_save_employee_runtime_creates_runtime_section(tmp_path, monkeypatch):
    """save_employee_runtime writes runtime fields into profile.yaml runtime: section."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "EMPLOYEES_DIR", tmp_path)

    emp_dir = tmp_path / "00100"
    emp_dir.mkdir()
    profile = emp_dir / "profile.yaml"
    profile.write_text("name: TestBot\nrole: Engineer\n")

    await store.save_employee_runtime("00100", status="working", current_task_summary="coding")

    data = yaml.safe_load(profile.read_text())
    assert data["runtime"]["status"] == "working"
    assert data["runtime"]["current_task_summary"] == "coding"
    assert "employees" in store._dirty


def test_load_employee_reads_profile_with_runtime(tmp_path, monkeypatch):
    """load_employee reads profile.yaml and merges runtime section."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "EMPLOYEES_DIR", tmp_path)

    emp_dir = tmp_path / "00100"
    emp_dir.mkdir()
    profile = emp_dir / "profile.yaml"
    profile.write_text("name: TestBot\nrole: Engineer\nruntime:\n  status: working\n")

    result = store.load_employee("00100")
    assert result["name"] == "TestBot"
    assert result["runtime"]["status"] == "working"


def test_load_all_employees_reads_all_dirs(tmp_path, monkeypatch):
    """load_all_employees reads all employee directories."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "EMPLOYEES_DIR", tmp_path)

    for eid in ["00002", "00100"]:
        d = tmp_path / eid
        d.mkdir()
        (d / "profile.yaml").write_text(f"name: Emp{eid}\nrole: Engineer\n")

    result = store.load_all_employees()
    assert len(result) == 2
    assert result["00002"]["name"] == "Emp00002"
    assert result["00100"]["name"] == "Emp00100"


@pytest.mark.asyncio
async def test_save_employee_merges_updates(tmp_path, monkeypatch):
    """save_employee merges updates into existing profile.yaml."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "EMPLOYEES_DIR", tmp_path)

    emp_dir = tmp_path / "00100"
    emp_dir.mkdir()
    (emp_dir / "profile.yaml").write_text("name: OldName\nrole: Engineer\nskills:\n- python\n")

    await store.save_employee("00100", {"name": "NewName", "level": 2})

    data = yaml.safe_load((emp_dir / "profile.yaml").read_text())
    assert data["name"] == "NewName"
    assert data["level"] == 2
    assert data["skills"] == ["python"]  # preserved


def test_read_yaml_list_returns_list(tmp_path):
    from onemancompany.core.store import _read_yaml_list
    p = tmp_path / "items.yaml"
    p.write_text("- name: A\n- name: B\n")
    result = _read_yaml_list(p)
    assert len(result) == 2
    assert result[0]["name"] == "A"

def test_read_yaml_list_empty_returns_empty(tmp_path):
    from onemancompany.core.store import _read_yaml_list
    assert _read_yaml_list(tmp_path / "missing.yaml") == []

def test_read_yaml_list_non_list_returns_empty(tmp_path):
    from onemancompany.core.store import _read_yaml_list
    p = tmp_path / "notlist.yaml"
    p.write_text("key: value\n")
    assert _read_yaml_list(p) == []

@pytest.mark.asyncio
async def test_save_project_status_updates_yaml(tmp_path, monkeypatch):
    from onemancompany.core import store
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path)
    pdir = tmp_path / "proj-001"
    pdir.mkdir()
    (pdir / "project.yaml").write_text("task: Build thing\nstatus: in_progress\n")
    await store.save_project_status("proj-001", "completed", completed_at="2026-03-11")
    data = yaml.safe_load((pdir / "project.yaml").read_text())
    assert data["status"] == "completed"
    assert data["completed_at"] == "2026-03-11"
    assert data["task"] == "Build thing"

def test_load_rooms_reads_yaml(tmp_path, monkeypatch):
    from onemancompany.core import store
    rooms_dir = tmp_path / "rooms"
    rooms_dir.mkdir()
    monkeypatch.setattr(store, "_rooms_dir", lambda: rooms_dir)
    (rooms_dir / "room-a.yaml").write_text("id: room-a\nname: Alpha\ncapacity: 4\n")
    (rooms_dir / "room-b.yaml").write_text("id: room-b\nname: Beta\ncapacity: 8\n")
    result = store.load_rooms()
    assert len(result) == 2
    names = {r["name"] for r in result}
    assert names == {"Alpha", "Beta"}

@pytest.mark.asyncio
async def test_save_room_updates_booking(tmp_path, monkeypatch):
    from onemancompany.core import store
    rooms_dir = tmp_path / "rooms"
    rooms_dir.mkdir()
    monkeypatch.setattr(store, "_rooms_dir", lambda: rooms_dir)
    (rooms_dir / "room-a.yaml").write_text("id: room-a\nname: Alpha\nis_booked: false\n")
    await store.save_room("room-a", {"is_booked": True, "booked_by": "00002"})
    data = yaml.safe_load((rooms_dir / "room-a.yaml").read_text())
    assert data["is_booked"] is True
    assert data["booked_by"] == "00002"

@pytest.mark.asyncio
async def test_append_activity_appends_entry(tmp_path, monkeypatch):
    from onemancompany.core import store
    monkeypatch.setattr(store, "COMPANY_DIR", tmp_path)
    await store.append_activity({"type": "hired", "employee": "00100"})
    await store.append_activity({"type": "fired", "employee": "00101"})
    data = store._read_yaml_list(tmp_path / "activity_log.yaml")
    assert len(data) == 2
    assert data[0]["type"] == "hired"

def test_load_tools_reads_tool_dirs(tmp_path, monkeypatch):
    from onemancompany.core import store
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path)
    tools_dir = tmp_path / "company" / "assets" / "tools"
    tool_a = tools_dir / "tool-a"
    tool_a.mkdir(parents=True)
    (tool_a / "tool.yaml").write_text("id: tool-a\nname: Hammer\n")
    result = store.load_tools()
    assert len(result) == 1
    assert result[0]["name"] == "Hammer"

@pytest.mark.asyncio
async def test_save_tool_writes_yaml(tmp_path, monkeypatch):
    from onemancompany.core import store
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path)
    tools_dir = tmp_path / "company" / "assets" / "tools"
    tool_dir = tools_dir / "my-tool"
    tool_dir.mkdir(parents=True)
    (tool_dir / "tool.yaml").write_text("id: my-tool\nname: OldName\n")
    await store.save_tool("my-tool", {"id": "my-tool", "name": "NewName"})
    data = yaml.safe_load((tool_dir / "tool.yaml").read_text())
    assert data["name"] == "NewName"

@pytest.mark.asyncio
async def test_save_tree_writes_yaml(tmp_path):
    from onemancompany.core import store
    pdir = tmp_path / "proj-001"
    pdir.mkdir()
    tree_data = {"root": "node-1", "nodes": [{"id": "node-1", "status": "pending"}]}
    await store.save_tree(str(pdir), tree_data)
    data = yaml.safe_load((pdir / "task_tree.yaml").read_text())
    assert data["root"] == "node-1"

@pytest.mark.asyncio
async def test_save_culture_writes_list(tmp_path, monkeypatch):
    from onemancompany.core import store
    monkeypatch.setattr(store, "COMPANY_DIR", tmp_path)
    await store.save_culture([{"value": "Move fast"}])
    data = store._read_yaml_list(tmp_path / "company_culture.yaml")
    assert len(data) == 1
    assert data[0]["value"] == "Move fast"

@pytest.mark.asyncio
async def test_save_overhead_writes_yaml(tmp_path, monkeypatch):
    from onemancompany.core import store
    monkeypatch.setattr(store, "COMPANY_DIR", tmp_path)
    await store.save_overhead({"company_tokens": 1000000, "total_cost": 42.5})
    data = store._read_yaml(tmp_path / "overhead.yaml")
    assert data["company_tokens"] == 1000000

@pytest.mark.asyncio
async def test_save_candidates_writes_yaml(tmp_path, monkeypatch):
    from onemancompany.core import store
    monkeypatch.setattr(store, "COMPANY_DIR", tmp_path)
    await store.save_candidates("batch-001", {"candidates": [{"name": "Alice"}]})
    data = store._read_yaml(tmp_path / "candidates" / "batch-001.yaml")
    assert data["candidates"][0]["name"] == "Alice"
