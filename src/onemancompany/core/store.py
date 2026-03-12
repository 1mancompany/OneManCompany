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


# ---------------------------------------------------------------------------
# Room helpers
# ---------------------------------------------------------------------------

def _rooms_dir() -> Path:
    return DATA_ROOT / "company" / "assets" / "rooms"


# ---------------------------------------------------------------------------
# Project reads/writes
# ---------------------------------------------------------------------------

def load_project(project_id: str) -> dict:
    return _read_yaml(PROJECTS_DIR / project_id / "project.yaml")


async def save_project_status(project_id: str, status: str, **extra) -> None:
    path = PROJECTS_DIR / project_id / "project.yaml"
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        data["status"] = status
        data.update(extra)
        _write_yaml(path, data)
    mark_dirty("task_queue")


# ---------------------------------------------------------------------------
# Room reads/writes
# ---------------------------------------------------------------------------

def load_rooms() -> list[dict]:
    rdir = _rooms_dir()
    if not rdir.exists():
        return []
    results = []
    for f in sorted(rdir.iterdir()):
        if f.suffix in (".yaml", ".yml") and not f.name.endswith("_chat.yaml"):
            results.append(_read_yaml(f))
    return results


def load_room(room_id: str) -> dict:
    return _read_yaml(_rooms_dir() / f"{room_id}.yaml")


async def save_room(room_id: str, updates: dict) -> None:
    path = _rooms_dir() / f"{room_id}.yaml"
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        data.update(updates)
        _write_yaml(path, data)
    mark_dirty("rooms")


def load_room_chat(room_id: str) -> list[dict]:
    return _read_yaml_list(_rooms_dir() / f"{room_id}_chat.yaml")


async def append_room_chat(room_id: str, message: dict) -> None:
    path = _rooms_dir() / f"{room_id}_chat.yaml"
    async with _get_lock(str(path)):
        messages = _read_yaml_list(path)
        messages.append(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(messages, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    mark_dirty("rooms")


# ---------------------------------------------------------------------------
# Tool reads/writes
# ---------------------------------------------------------------------------

def load_tools() -> list[dict]:
    tools_dir = DATA_ROOT / "company" / "assets" / "tools"
    if not tools_dir.exists():
        return []
    results = []
    for tdir in sorted(tools_dir.iterdir()):
        if not tdir.is_dir():
            continue
        tyaml = tdir / "tool.yaml"
        if tyaml.exists():
            results.append(_read_yaml(tyaml))
    return results


async def save_tool(slug: str, data: dict) -> None:
    tools_dir = DATA_ROOT / "company" / "assets" / "tools"
    path = tools_dir / slug / "tool.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty("tools")


# ---------------------------------------------------------------------------
# Task tree reads/writes
# ---------------------------------------------------------------------------

async def save_tree(project_dir: str, tree_data: dict) -> None:
    path = Path(project_dir) / "task_tree.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, tree_data)
    mark_dirty("task_queue")


# ---------------------------------------------------------------------------
# Company-level reads/writes
# ---------------------------------------------------------------------------

def load_activity_log() -> list[dict]:
    return _read_yaml_list(COMPANY_DIR / "activity_log.yaml")


async def append_activity(entry: dict) -> None:
    path = COMPANY_DIR / "activity_log.yaml"
    async with _get_lock(str(path)):
        log = _read_yaml_list(path)
        log.append(entry)
        if len(log) > 200:
            log = log[-200:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(log, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    mark_dirty("activity_log")


def append_activity_sync(entry: dict) -> None:
    """Synchronous version of append_activity for use in non-async contexts (e.g., LangChain tools)."""
    path = COMPANY_DIR / "activity_log.yaml"
    log = _read_yaml_list(path)
    log.append(entry)
    if len(log) > 200:
        log = log[-200:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(log, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    mark_dirty("activity_log")


def load_culture() -> list[dict]:
    return _read_yaml_list(COMPANY_DIR / "company_culture.yaml")


async def save_culture(items: list[dict]) -> None:
    path = COMPANY_DIR / "company_culture.yaml"
    async with _get_lock(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(items, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    mark_dirty("culture")


def load_direction() -> str:
    data = _read_yaml(COMPANY_DIR / "company_direction.yaml")
    return data.get("direction", "") if data else ""


async def save_direction(text: str) -> None:
    path = COMPANY_DIR / "company_direction.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, {"direction": text})
    mark_dirty("direction")


def load_sales_tasks() -> list[dict]:
    return _read_yaml_list(COMPANY_DIR / "sales" / "tasks.yaml")


async def save_sales_tasks(tasks: list[dict]) -> None:
    path = COMPANY_DIR / "sales" / "tasks.yaml"
    async with _get_lock(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(tasks, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    mark_dirty("sales_tasks")


# ---------------------------------------------------------------------------
# Candidate reads/writes
# ---------------------------------------------------------------------------

def load_candidates(batch_id: str) -> dict:
    return _read_yaml(COMPANY_DIR / "candidates" / f"{batch_id}.yaml")


async def save_candidates(batch_id: str, data: dict) -> None:
    path = COMPANY_DIR / "candidates" / f"{batch_id}.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty("candidates")


# ---------------------------------------------------------------------------
# 1-on-1 chat history
# ---------------------------------------------------------------------------

def load_oneonone(emp_id: str) -> list[dict]:
    return _read_yaml_list(EMPLOYEES_DIR / emp_id / "oneonone_history.yaml")


async def append_oneonone(emp_id: str, message: dict) -> None:
    path = EMPLOYEES_DIR / emp_id / "oneonone_history.yaml"
    async with _get_lock(str(path)):
        history = _read_yaml_list(path)
        history.append(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(history, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    mark_dirty("employees")


# ---------------------------------------------------------------------------
# Overhead / token tracking
# ---------------------------------------------------------------------------

def load_overhead() -> dict:
    return _read_yaml(COMPANY_DIR / "overhead.yaml")


async def save_overhead(data: dict) -> None:
    path = COMPANY_DIR / "overhead.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty("overhead")
