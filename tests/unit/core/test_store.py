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
