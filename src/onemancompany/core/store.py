"""Unified read/write layer — disk is the single source of truth.

All persistent writes go through this module. Each function:
1. Acquires a per-file asyncio.Lock
2. Writes to disk (YAML) immediately
3. Marks the relevant resource category as dirty for the next sync tick
4. Does NOT update any in-memory cache

Reads always go to disk. No caching.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from onemancompany.core.config import (
    COMPANY_DIR,
    DATA_ROOT,
    EMPLOYEES_DIR,
    PROJECTS_DIR,
)

# ---------------------------------------------------------------------------
# Low-level YAML I/O
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict:
    """Read a YAML file, return dict. Returns {} if file missing or empty."""
    try:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8")
        return yaml.safe_load(text) or {}
    except Exception as e:
        logger.error("Failed to read {}: {}", path, e)
        return {}


def _write_yaml(path: Path, data: dict) -> None:
    """Write dict to YAML file. Creates parent dirs if needed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error("Failed to write {}: {}", path, e)
        raise


def _read_yaml_list(path: Path) -> list:
    """Read a YAML file that contains a list. Returns [] if missing."""
    try:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        result = yaml.safe_load(text)
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error("Failed to read list {}: {}", path, e)
        return []


# ---------------------------------------------------------------------------
# Per-file asyncio locks
# ---------------------------------------------------------------------------

_file_locks: dict[str, asyncio.Lock] = {}


def _get_lock(file_path: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for the given file path."""
    if file_path not in _file_locks:
        _file_locks[file_path] = asyncio.Lock()
    return _file_locks[file_path]


# ---------------------------------------------------------------------------
# Dirty tracking
# ---------------------------------------------------------------------------

_dirty: set[str] = set()


def mark_dirty(*categories: str) -> None:
    """Mark resource categories as dirty for the next sync tick."""
    _dirty.update(categories)


def flush_dirty() -> list[str]:
    """Called by sync tick. Returns and clears the dirty set."""
    changed = list(_dirty)
    _dirty.clear()
    return changed


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _employee_profile_path(emp_id: str) -> Path:
    return EMPLOYEES_DIR / emp_id / "profile.yaml"


def _ex_employee_profile_path(emp_id: str) -> Path:
    return DATA_ROOT / "company" / "human_resource" / "ex-employees" / emp_id / "profile.yaml"


# ---------------------------------------------------------------------------
# Employee reads
# ---------------------------------------------------------------------------

def load_employee(emp_id: str) -> dict:
    """Read profile.yaml for a single employee. Returns full dict including runtime."""
    return _read_yaml(_employee_profile_path(emp_id))


def load_all_employees() -> dict[str, dict]:
    """Read all employee profile.yamls from disk. Returns {emp_id: profile_dict}."""
    result: dict[str, dict] = {}
    if not EMPLOYEES_DIR.exists():
        return result
    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        profile_path = emp_dir / "profile.yaml"
        if profile_path.exists():
            result[emp_dir.name] = _read_yaml(profile_path)
    return result


def load_ex_employees() -> dict[str, dict]:
    """Read all ex-employee profile.yamls."""
    ex_dir = DATA_ROOT / "company" / "human_resource" / "ex-employees"
    result: dict[str, dict] = {}
    if not ex_dir.exists():
        return result
    for emp_dir in sorted(ex_dir.iterdir()):
        if not emp_dir.is_dir():
            continue
        profile_path = emp_dir / "profile.yaml"
        if profile_path.exists():
            result[emp_dir.name] = _read_yaml(profile_path)
    return result


def load_employee_guidance(emp_id: str) -> list[str]:
    """Read guidance.yaml for an employee. Returns list of guidance notes."""
    path = EMPLOYEES_DIR / emp_id / "guidance.yaml"
    data = _read_yaml(path)
    return data.get("notes", []) if data else []


def load_employee_work_principles(emp_id: str) -> str:
    """Read work_principles.md for an employee."""
    path = EMPLOYEES_DIR / emp_id / "work_principles.md"
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Employee writes
# ---------------------------------------------------------------------------

async def save_employee(emp_id: str, updates: dict) -> None:
    """Merge updates into employee profile.yaml, write immediately."""
    path = _employee_profile_path(emp_id)
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        data.update(updates)
        _write_yaml(path, data)
    mark_dirty("employees")


async def save_employee_runtime(emp_id: str, **fields) -> None:
    """Update runtime: section of employee profile.yaml."""
    path = _employee_profile_path(emp_id)
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        runtime = data.setdefault("runtime", {})
        runtime.update(fields)
        _write_yaml(path, data)
    mark_dirty("employees")


async def save_ex_employee(emp_id: str, data: dict) -> None:
    """Write ex-employee profile to disk."""
    path = _ex_employee_profile_path(emp_id)
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty("ex_employees")


async def save_guidance(emp_id: str, notes: list[str]) -> None:
    """Write guidance.yaml for an employee."""
    path = EMPLOYEES_DIR / emp_id / "guidance.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, {"notes": notes})
    mark_dirty("employees")


async def save_work_principles(emp_id: str, text: str) -> None:
    """Write work_principles.md for an employee."""
    path = EMPLOYEES_DIR / emp_id / "work_principles.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    mark_dirty("employees")
