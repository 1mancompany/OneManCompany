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
from enum import Enum
from pathlib import Path
from typing import Any  # noqa: F401 — used by callers re-exporting

import yaml
from loguru import logger

from onemancompany.core.config import (
    COMPANY_CULTURE_FILE,
    COMPANY_DIRECTION_FILE,
    COMPANY_DIR,
    DATA_ROOT,  # noqa: F401 — re-exported, used by test fixtures
    ENCODING_UTF8,
    PF_GUIDANCE_NOTES,
    PF_WORK_PRINCIPLES,
    PROJECTS_DIR,  # noqa: F401 — re-exported, used by test fixtures
    DirtyCategory,
    EMPLOYEES_DIR,
    EX_EMPLOYEES_DIR,
    GUIDANCE_FILENAME,
    PET_FACILITIES_DIR,
    PET_FACILITY_TYPES_DIR,
    PET_INSTANCES_DIR,
    PET_SPECIES_DIR,
    PROFILE_FILENAME,
    ROOMS_DIR,
    TASK_TREE_FILENAME,
    TOOL_YAML_FILENAME,
    TOOLS_DIR,
)

# ---------------------------------------------------------------------------
# Filename constants (single-file, used only in store.py)
# ---------------------------------------------------------------------------
WORK_PRINCIPLES_FILENAME = "work_principles.md"
_GUIDANCE_NOTES_KEY = "notes"  # key inside guidance.yaml
ACTIVITY_LOG_FILENAME = "activity_log.yaml"
OVERHEAD_FILENAME = "overhead.yaml"
TASK_INDEX_FILENAME = "task_index.yaml"
ONEONONE_HISTORY_FILENAME = "oneonone_history.yaml"
COMPANY_CULTURE_FILENAME = "company_culture.yaml"
SALES_TASKS_PATH = COMPANY_DIR / "sales" / "tasks.yaml"
CANDIDATES_DIR = COMPANY_DIR / "candidates"

# ---------------------------------------------------------------------------
# Low-level YAML I/O
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict:
    """Read a YAML file, return dict. Returns {} if file missing or empty."""
    try:
        if not path.exists():
            return {}
        text = path.read_text(encoding=ENCODING_UTF8)
        return yaml.safe_load(text) or {}
    except Exception as e:
        logger.error("Failed to read {}: {}", path, e)
        return {}


def _enum_representer(dumper: yaml.Dumper, data: Enum) -> yaml.ScalarNode:
    """Serialize str enums as plain strings to avoid !!python/object tags."""
    return dumper.represent_str(str(data.value))


# Register for all str+Enum subclasses used in the codebase
_yaml_dumper = yaml.Dumper
_yaml_dumper.add_multi_representer(Enum, _enum_representer)


def _write_yaml(path: Path, data: dict) -> None:
    """Write dict to YAML file atomically (temp file + os.replace).

    Crash during write leaves the original file intact.
    """
    try:
        content = yaml.dump(data, Dumper=_yaml_dumper, allow_unicode=True, default_flow_style=False, sort_keys=False)
        _atomic_write_text(path, content)
    except Exception as e:
        logger.error("Failed to write {}: {}", path, e)
        raise


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text to file atomically (temp file + os.replace)."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=ENCODING_UTF8) as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError as exc:
            logger.debug("Failed to clean up temp file {}: {}", tmp_path, exc)
        raise


def _read_yaml_list(path: Path) -> list:
    """Read a YAML file that contains a list. Returns [] if missing."""
    try:
        if not path.exists():
            return []
        text = path.read_text(encoding=ENCODING_UTF8)
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
    """Mark resource categories as dirty for the next sync tick.

    Also invalidates read cache for the affected categories.
    """
    _dirty.update(categories)
    for cat in categories:
        _read_cache_invalidate(cat)


def flush_dirty() -> list[str]:
    """Called by sync tick. Returns and clears the dirty set."""
    changed = list(_dirty)
    _dirty.clear()
    return changed


def _build_path_dirty_registry() -> list[tuple[Path, DirtyCategory]]:
    """Build path→category registry from config directories.

    Sorted by path depth (deepest first) so most-specific match wins.
    Paths are resolved at build time for consistent matching.

    Categories NOT in this registry (handled by dedicated store functions):
      PROJECTS — project metadata changes (create_iteration, save_project_status,
        LLM rename) already call mark_dirty(PROJECTS) explicitly. Agent file
        writes to PROJECTS_DIR are deliverables, not metadata.
      CULTURE, DIRECTION — single YAML files, written via dedicated store functions
      ACTIVITY_LOG, OVERHEAD — single YAML files in COMPANY_DIR
      SALES_TASKS — single file at company/sales/tasks.yaml
      OFFICE_LAYOUT — event-driven, no disk directory
    """
    from onemancompany.core.config import (
        EMPLOYEES_DIR, EX_EMPLOYEES_DIR, ROOMS_DIR, TOOLS_DIR,
        DirtyCategory,
    )
    entries = [
        (EMPLOYEES_DIR, DirtyCategory.EMPLOYEES),
        (EX_EMPLOYEES_DIR, DirtyCategory.EX_EMPLOYEES),
        (ROOMS_DIR, DirtyCategory.ROOMS),
        (TOOLS_DIR, DirtyCategory.TOOLS),
        (CANDIDATES_DIR, DirtyCategory.CANDIDATES),
    ]
    # Resolve once at build time, sort deepest first
    resolved = [(d.resolve(), cat) for d, cat in entries]
    return sorted(resolved, key=lambda e: len(e[0].parts), reverse=True)


_path_dirty_registry: list[tuple[Path, DirtyCategory]] | None = None


def mark_dirty_for_path(path: Path) -> None:
    """Auto-detect dirty category from file path and mark it.

    Uses a registry mapping directory prefixes to DirtyCategory values.
    Called by generic write/edit tools after file operations to ensure
    the sync tick broadcasts changes to the frontend.
    """
    global _path_dirty_registry
    if _path_dirty_registry is None:
        _path_dirty_registry = _build_path_dirty_registry()

    try:
        resolved = path.resolve()
        for dir_path, category in _path_dirty_registry:
            if resolved.is_relative_to(dir_path):
                mark_dirty(category)
                return
    except Exception as exc:
        logger.warning("mark_dirty_for_path failed for {}: {}", path, exc)


# ---------------------------------------------------------------------------
# Read cache — dirty-aware, short-lived cache for read-heavy bootstrap path
# ---------------------------------------------------------------------------

import time as _time  # noqa: E402

# {cache_key: (value, timestamp)}
_read_cache: dict[str, tuple[Any, float]] = {}
# Maps DirtyCategory value → set of cache keys to invalidate
_cache_key_to_category: dict[str, set[str]] = {}

_CACHE_TTL_SECONDS = 3.0  # Matches sync tick interval


def _read_cache_get(key: str) -> Any | None:
    """Get cached value if present and not expired. Returns None on miss."""
    entry = _read_cache.get(key)
    if entry is None:
        return None
    value, ts = entry
    if (_time.monotonic() - ts) > _CACHE_TTL_SECONDS:
        _read_cache.pop(key, None)
        return None
    return value


def _read_cache_set(key: str, value: Any, category: str) -> None:
    """Store a value in the read cache, tagged with its dirty category."""
    _read_cache[key] = (value, _time.monotonic())
    _cache_key_to_category.setdefault(category, set()).add(key)


def _read_cache_invalidate(category: str) -> None:
    """Evict all cache entries for a dirty category."""
    keys = _cache_key_to_category.pop(category, set())
    for k in keys:
        _read_cache.pop(k, None)


def cache_clear_all() -> None:
    """Clear entire read cache. Useful for tests."""
    _read_cache.clear()
    _cache_key_to_category.clear()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _employee_profile_path(emp_id: str) -> Path:
    return EMPLOYEES_DIR / emp_id / PROFILE_FILENAME


def _ex_employee_profile_path(emp_id: str) -> Path:
    return EX_EMPLOYEES_DIR / emp_id / PROFILE_FILENAME


# ---------------------------------------------------------------------------
# Employee reads
# ---------------------------------------------------------------------------

def load_employee(emp_id: str) -> dict:
    """Read profile.yaml for a single employee. Returns full dict including runtime.

    Also loads work_principles.md and guidance.yaml into the dict so all
    callers get these fields without separate file reads.
    """
    data = _read_yaml(_employee_profile_path(emp_id))
    if data:
        data[PF_WORK_PRINCIPLES] = load_employee_work_principles(emp_id)
        data[PF_GUIDANCE_NOTES] = load_employee_guidance(emp_id)
    return data


def load_all_employees() -> dict[str, dict]:
    """Read all employee profile.yamls from disk. Returns {emp_id: profile_dict}.

    Also loads work_principles and guidance_notes from their separate files.
    Uses dirty-aware cache to avoid redundant disk reads during rapid bootstrap/tick.
    """
    cached = _read_cache_get("all_employees")
    if cached is not None:
        # Return deep copy so callers can mutate without poisoning cache
        import copy
        return copy.deepcopy(cached)

    result: dict[str, dict] = {}
    if not EMPLOYEES_DIR.exists():
        return result
    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        profile_path = emp_dir / PROFILE_FILENAME
        if profile_path.exists():
            data = _read_yaml(profile_path)
            emp_id = emp_dir.name
            data[PF_WORK_PRINCIPLES] = load_employee_work_principles(emp_id)
            data[PF_GUIDANCE_NOTES] = load_employee_guidance(emp_id)
            result[emp_id] = data
    _read_cache_set("all_employees", result, DirtyCategory.EMPLOYEES)
    return result


def load_ex_employees() -> dict[str, dict]:
    """Read all ex-employee profile.yamls."""
    ex_dir = EX_EMPLOYEES_DIR
    result: dict[str, dict] = {}
    if not ex_dir.exists():
        return result
    for emp_dir in sorted(ex_dir.iterdir()):
        if not emp_dir.is_dir():
            continue
        profile_path = emp_dir / PROFILE_FILENAME
        if profile_path.exists():
            result[emp_dir.name] = _read_yaml(profile_path)
    return result


def load_employee_guidance(emp_id: str) -> list[str]:
    """Read guidance.yaml for an employee. Returns list of guidance notes."""
    path = EMPLOYEES_DIR / emp_id / GUIDANCE_FILENAME
    data = _read_yaml(path)
    if not data:
        return []
    if isinstance(data, list):
        return data
    return data.get(_GUIDANCE_NOTES_KEY, []) if isinstance(data, dict) else []


def load_employee_work_principles(emp_id: str) -> str:
    """Read work_principles.md for an employee."""
    path = EMPLOYEES_DIR / emp_id / WORK_PRINCIPLES_FILENAME
    try:
        return path.read_text(encoding=ENCODING_UTF8) if path.exists() else ""
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
    mark_dirty(DirtyCategory.EMPLOYEES)


async def save_employee_runtime(emp_id: str, **fields) -> None:
    """Update runtime: section of employee profile.yaml."""
    path = _employee_profile_path(emp_id)
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        runtime = data.setdefault("runtime", {})
        runtime.update(fields)
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.EMPLOYEES)


async def save_ex_employee(emp_id: str, data: dict) -> None:
    """Write ex-employee profile to disk."""
    path = _ex_employee_profile_path(emp_id)
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.EX_EMPLOYEES)


async def save_guidance(emp_id: str, notes: list[str]) -> None:
    """Write guidance.yaml for an employee."""
    path = EMPLOYEES_DIR / emp_id / GUIDANCE_FILENAME
    async with _get_lock(str(path)):
        _write_yaml(path, {_GUIDANCE_NOTES_KEY: notes})
    mark_dirty(DirtyCategory.EMPLOYEES)


async def save_work_principles(emp_id: str, text: str) -> None:
    """Write work_principles.md for an employee."""
    path = EMPLOYEES_DIR / emp_id / WORK_PRINCIPLES_FILENAME
    _atomic_write_text(path, text)
    mark_dirty(DirtyCategory.EMPLOYEES)


# ---------------------------------------------------------------------------
# Room helpers
# ---------------------------------------------------------------------------

def _rooms_dir() -> Path:
    return ROOMS_DIR


# ---------------------------------------------------------------------------
# Project reads/writes
# ---------------------------------------------------------------------------

def load_project(project_id: str) -> dict:
    from onemancompany.core.project_archive import load_project as _load_project
    return _load_project(project_id) or {}


async def save_project_status(project_id: str, status: str, **extra) -> None:
    from onemancompany.core.project_archive import update_project_status
    update_project_status(project_id, status, **extra)
    mark_dirty(DirtyCategory.PROJECTS)


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
            data = _read_yaml(f)
            data.setdefault("id", f.stem)
            results.append(data)
    return results


def load_room(room_id: str) -> dict:
    data = _read_yaml(_rooms_dir() / f"{room_id}.yaml")
    if data:
        data.setdefault("id", room_id)
    return data


async def save_room(room_id: str, updates: dict) -> None:
    path = _rooms_dir() / f"{room_id}.yaml"
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        data.update(updates)
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.ROOMS)


def load_room_chat(room_id: str) -> list[dict]:
    return _read_yaml_list(_rooms_dir() / f"{room_id}_chat.yaml")


async def append_room_chat(room_id: str, message: dict) -> None:
    path = _rooms_dir() / f"{room_id}_chat.yaml"
    async with _get_lock(str(path)):
        messages = _read_yaml_list(path)
        messages.append(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, yaml.dump(messages, allow_unicode=True, default_flow_style=False))
    mark_dirty(DirtyCategory.ROOMS)


async def clear_room_chat(room_id: str) -> None:
    """Clear all chat messages for a room (after archiving to meeting minutes)."""
    path = _rooms_dir() / f"{room_id}_chat.yaml"
    async with _get_lock(str(path)):
        _atomic_write_text(path, yaml.dump([], allow_unicode=True, default_flow_style=False))
    mark_dirty(DirtyCategory.ROOMS)


# ---------------------------------------------------------------------------
# Meeting minutes archive
# ---------------------------------------------------------------------------

MEETING_MINUTES_DIR = COMPANY_DIR / "meeting_minutes"


def archive_meeting(room_id: str, record: dict) -> str:
    """Archive a meeting's chat to meeting_minutes/{room_id}_{timestamp}.yaml.

    Returns the minute_id (filename stem).
    """
    from datetime import datetime

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    minute_id = f"{room_id}_{ts}"
    path = MEETING_MINUTES_DIR / f"{minute_id}.yaml"
    _write_yaml(path, {**record, "minute_id": minute_id})
    return minute_id


def load_meeting_minutes(room_id: str | None = None) -> list[dict]:
    """List archived meeting minutes, optionally filtered by room_id. Newest first."""
    if not MEETING_MINUTES_DIR.exists():
        return []
    results = []
    for p in sorted(MEETING_MINUTES_DIR.iterdir(), reverse=True):
        if not p.suffix == ".yaml":
            continue
        if room_id and not p.stem.startswith(room_id + "_"):
            continue
        data = _read_yaml(p)
        if data:
            data.setdefault("minute_id", p.stem)
            results.append(data)
    return results


def load_meeting_minute(minute_id: str) -> dict:
    """Load a single meeting minute by its ID."""
    path = MEETING_MINUTES_DIR / f"{minute_id}.yaml"
    return _read_yaml(path)


# ---------------------------------------------------------------------------
# Tool reads/writes
# ---------------------------------------------------------------------------

def load_tools() -> list[dict]:
    cached = _read_cache_get("tools")
    if cached is not None:
        return list(cached)
    tools_dir = DATA_ROOT / "company" / "assets" / "tools"
    if not tools_dir.exists():
        return []
    results = []
    for tdir in sorted(tools_dir.iterdir()):
        if not tdir.is_dir():
            continue
        tyaml = tdir / TOOL_YAML_FILENAME
        if tyaml.exists():
            results.append(_read_yaml(tyaml))
    _read_cache_set("tools", results, DirtyCategory.TOOLS)
    return results


async def save_tool(slug: str, data: dict) -> None:
    tools_dir = DATA_ROOT / "company" / "assets" / "tools"
    path = tools_dir / slug / TOOL_YAML_FILENAME
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.TOOLS)


# ---------------------------------------------------------------------------
# Task tree reads/writes
# ---------------------------------------------------------------------------

async def save_tree(project_dir: str, tree_data: dict) -> None:
    path = Path(project_dir) / TASK_TREE_FILENAME
    async with _get_lock(str(path)):
        _write_yaml(path, tree_data)
    mark_dirty(DirtyCategory.PROJECTS)


# ---------------------------------------------------------------------------
# Company-level reads/writes
# ---------------------------------------------------------------------------

def load_activity_log() -> list[dict]:
    cached = _read_cache_get("activity_log")
    if cached is not None:
        return list(cached)  # shallow copy — entries are dicts, safe for slicing
    result = _read_yaml_list(COMPANY_DIR / ACTIVITY_LOG_FILENAME)
    _read_cache_set("activity_log", result, DirtyCategory.ACTIVITY_LOG)
    return result


async def append_activity(entry: dict) -> None:
    path = COMPANY_DIR / ACTIVITY_LOG_FILENAME
    async with _get_lock(str(path)):
        log = _read_yaml_list(path)
        log.append(entry)
        if len(log) > 200:
            log = log[-200:]
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, yaml.dump(log, allow_unicode=True, default_flow_style=False))
    mark_dirty(DirtyCategory.ACTIVITY_LOG)


def append_activity_sync(entry: dict) -> None:
    """Synchronous version of append_activity for use in non-async contexts (e.g., LangChain tools)."""
    path = COMPANY_DIR / ACTIVITY_LOG_FILENAME
    log = _read_yaml_list(path)
    log.append(entry)
    if len(log) > 200:
        log = log[-200:]
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, yaml.dump(log, allow_unicode=True, default_flow_style=False))
    mark_dirty(DirtyCategory.ACTIVITY_LOG)


def load_culture() -> list[dict]:
    return _read_yaml_list(COMPANY_DIR / COMPANY_CULTURE_FILENAME)


async def save_culture(items: list[dict]) -> None:
    path = COMPANY_DIR / COMPANY_CULTURE_FILENAME
    async with _get_lock(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, yaml.dump(items, allow_unicode=True, default_flow_style=False))
    mark_dirty(DirtyCategory.CULTURE)


def load_direction() -> str:
    data = _read_yaml(COMPANY_DIRECTION_FILE)
    return data.get("direction", "") if data else ""


async def save_direction(text: str) -> None:
    path = COMPANY_DIRECTION_FILE
    async with _get_lock(str(path)):
        _write_yaml(path, {"direction": text})
    mark_dirty(DirtyCategory.DIRECTION)


def load_sales_tasks() -> list[dict]:
    return _read_yaml_list(SALES_TASKS_PATH)


async def save_sales_tasks(tasks: list[dict]) -> None:
    path = SALES_TASKS_PATH
    async with _get_lock(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, yaml.dump(tasks, allow_unicode=True, default_flow_style=False))
    mark_dirty(DirtyCategory.SALES_TASKS)


def save_sales_tasks_sync(tasks: list[dict]) -> None:
    """Synchronous version of save_sales_tasks for use in non-async contexts (e.g., LangChain tools)."""
    path = SALES_TASKS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, yaml.dump(tasks, allow_unicode=True, default_flow_style=False))
    mark_dirty(DirtyCategory.SALES_TASKS)


# ---------------------------------------------------------------------------
# Employee task index — per-employee list of (node_id, tree_path) pointers
# ---------------------------------------------------------------------------


def load_task_index(employee_id: str) -> list[dict]:
    """Read the task index for an employee.

    Returns list of dicts: [{node_id, tree_path}, ...]
    """
    path = EMPLOYEES_DIR / employee_id / TASK_INDEX_FILENAME
    return _read_yaml_list(path)


def save_task_index(employee_id: str, entries: list[dict]) -> None:
    """Overwrite the task index for an employee (sync)."""
    path = EMPLOYEES_DIR / employee_id / TASK_INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, yaml.dump(entries, allow_unicode=True, default_flow_style=False))


def append_task_index_entry(employee_id: str, node_id: str, tree_path: str) -> None:
    """Add a task pointer to the employee's task index (idempotent)."""
    entries = load_task_index(employee_id)
    # Avoid duplicates
    for e in entries:
        if e.get("node_id") == node_id:
            return
    entries.append({"node_id": node_id, "tree_path": tree_path})
    # Cap at 100 most recent entries
    if len(entries) > 100:
        entries = entries[-100:]
    save_task_index(employee_id, entries)


# ---------------------------------------------------------------------------
# Candidate reads/writes
# ---------------------------------------------------------------------------

def load_candidates(batch_id: str) -> dict:
    return _read_yaml(COMPANY_DIR / "candidates" / f"{batch_id}.yaml")


async def save_candidates(batch_id: str, data: dict) -> None:
    path = COMPANY_DIR / "candidates" / f"{batch_id}.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.CANDIDATES)


# ---------------------------------------------------------------------------
# 1-on-1 chat history
# ---------------------------------------------------------------------------

def load_oneonone(emp_id: str) -> list[dict]:
    return _read_yaml_list(EMPLOYEES_DIR / emp_id / ONEONONE_HISTORY_FILENAME)


async def append_oneonone(emp_id: str, message: dict) -> None:
    path = EMPLOYEES_DIR / emp_id / ONEONONE_HISTORY_FILENAME
    async with _get_lock(str(path)):
        history = _read_yaml_list(path)
        history.append(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(path, yaml.dump(history, allow_unicode=True, default_flow_style=False))
    mark_dirty(DirtyCategory.EMPLOYEES)


# ---------------------------------------------------------------------------
# Overhead / token tracking
# ---------------------------------------------------------------------------

def load_overhead() -> dict:
    return _read_yaml(COMPANY_DIR / OVERHEAD_FILENAME)


async def save_overhead(data: dict) -> None:
    path = COMPANY_DIR / OVERHEAD_FILENAME
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty(DirtyCategory.OVERHEAD)


def save_overhead_sync(data: dict) -> None:
    """Synchronous version of save_overhead for use in non-async contexts (e.g., LangChain tools)."""
    path = COMPANY_DIR / OVERHEAD_FILENAME
    _write_yaml(path, data)
    mark_dirty(DirtyCategory.OVERHEAD)


# ---------------------------------------------------------------------------
# Async read wrappers — offload sync disk I/O to thread pool
# ---------------------------------------------------------------------------

async def aload_all_employees() -> dict[str, dict]:
    """Async wrapper: offloads load_all_employees() to thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, load_all_employees)


async def aload_activity_log() -> list[dict]:
    """Async wrapper: offloads load_activity_log() to thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, load_activity_log)


async def aload_tools() -> list[dict]:
    """Async wrapper: offloads load_tools() to thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, load_tools)


async def aload_rooms() -> list[dict]:
    """Async wrapper: offloads load_rooms() to thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, load_rooms)


async def aload_overhead() -> dict:
    """Async wrapper: offloads load_overhead() to thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, load_overhead)


# ---------------------------------------------------------------------------
# Pet species / instances
# ---------------------------------------------------------------------------

def load_pet_species() -> dict:
    """Scan PET_SPECIES_DIR for *.yaml, validate with SpeciesDefinition.

    Returns dict[species_id -> SpeciesDefinition]. Invalid files are logged
    and skipped.
    """
    from onemancompany.core.pet_models import SpeciesDefinition

    result: dict = {}
    if not PET_SPECIES_DIR.exists():
        return result
    for f in sorted(PET_SPECIES_DIR.iterdir()):
        if f.suffix not in (".yaml", ".yml"):
            continue
        data = _read_yaml(f)
        if not data:
            continue
        try:
            spec = SpeciesDefinition(**data)
            result[spec.id] = spec
        except Exception as exc:
            logger.warning("Skipping invalid pet species {}: {}", f.name, exc)
    return result


def load_all_pets_sync() -> dict:
    """Load all pet instance *.yaml from PET_INSTANCES_DIR.

    Returns dict[pet_id -> dict].
    """
    result: dict = {}
    if not PET_INSTANCES_DIR.exists():
        return result
    for f in sorted(PET_INSTANCES_DIR.iterdir()):
        if f.suffix not in (".yaml", ".yml"):
            continue
        data = _read_yaml(f)
        if data:
            result[f.stem] = data
    return result


def save_pet_sync(pet_id: str, data: dict) -> None:
    """Write pet instance to PET_INSTANCES_DIR/{pet_id}.yaml."""
    path = PET_INSTANCES_DIR / f"{pet_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, data)
    mark_dirty(DirtyCategory.PETS)


def delete_pet_sync(pet_id: str) -> None:
    """Delete PET_INSTANCES_DIR/{pet_id}.yaml if it exists."""
    path = PET_INSTANCES_DIR / f"{pet_id}.yaml"
    if path.exists():
        path.unlink()
        mark_dirty(DirtyCategory.PETS)


# ---------------------------------------------------------------------------
# Pet facility types / instances
# ---------------------------------------------------------------------------

def load_pet_facility_types() -> dict:
    """Load facility type definitions from PET_FACILITY_TYPES_DIR.

    Returns dict[type_id -> FacilityType]. Invalid files are logged and skipped.
    """
    from onemancompany.core.pet_models import FacilityType

    result: dict = {}
    if not PET_FACILITY_TYPES_DIR.exists():
        return result
    for f in sorted(PET_FACILITY_TYPES_DIR.iterdir()):
        if f.suffix not in (".yaml", ".yml"):
            continue
        data = _read_yaml(f)
        if not data:
            continue
        try:
            ft = FacilityType(**data)
            result[ft.id] = ft
        except Exception as exc:
            logger.warning("Skipping invalid facility type {}: {}", f.name, exc)
    return result


def load_facilities_sync() -> dict:
    """Load all facility instances from PET_FACILITIES_DIR.

    Returns dict[facility_id -> dict].
    """
    result: dict = {}
    if not PET_FACILITIES_DIR.exists():
        return result
    for f in sorted(PET_FACILITIES_DIR.iterdir()):
        if f.suffix not in (".yaml", ".yml"):
            continue
        data = _read_yaml(f)
        if data:
            result[f.stem] = data
    return result


def save_facility_sync(facility_id: str, data: dict) -> None:
    """Write facility instance to PET_FACILITIES_DIR/{facility_id}.yaml."""
    path = PET_FACILITIES_DIR / f"{facility_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, data)
    mark_dirty(DirtyCategory.PETS)


def delete_facility_sync(facility_id: str) -> None:
    """Delete PET_FACILITIES_DIR/{facility_id}.yaml if it exists."""
    path = PET_FACILITIES_DIR / f"{facility_id}.yaml"
    if path.exists():
        path.unlink()
        mark_dirty(DirtyCategory.PETS)


# ---------------------------------------------------------------------------
# Pet wallet (token economy)
# ---------------------------------------------------------------------------

_PET_WALLET_DEFAULT = {"tokens": 0, "projects_counted": 0, "tokens_spent": 0}


def load_pet_wallet() -> dict:
    """Read PETS_DIR/wallet.yaml, return default if missing."""
    from onemancompany.core.config import PETS_DIR

    path = PETS_DIR / "wallet.yaml"
    data = _read_yaml(path)
    if not data:
        return dict(_PET_WALLET_DEFAULT)
    # Ensure all keys present
    for k, v in _PET_WALLET_DEFAULT.items():
        data.setdefault(k, v)
    return data


def save_pet_wallet(data: dict) -> None:
    """Write wallet to PETS_DIR/wallet.yaml."""
    from onemancompany.core.config import PETS_DIR

    path = PETS_DIR / "wallet.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_yaml(path, data)
