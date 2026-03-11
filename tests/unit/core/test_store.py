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
