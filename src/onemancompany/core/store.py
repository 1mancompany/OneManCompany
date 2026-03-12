"""Disk-based data store — YAML I/O helpers with async locking.

This module provides the single-source-of-truth read/write layer for all
persistent business data.  Every write calls ``mark_dirty()`` so the
sync-tick broadcaster knows which sections need re-sending to frontends.

Currently a minimal implementation covering project status and the
dirty-tracking infrastructure.  Future tasks will migrate remaining
data access here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock

import yaml
from loguru import logger

from onemancompany.core.config import PROJECTS_DIR

# ---------------------------------------------------------------------------
# Dirty tracking — which data sections have changed since last sync tick
# ---------------------------------------------------------------------------

_dirty_sections: set[str] = set()
_dirty_lock = Lock()


def mark_dirty(section: str) -> None:
    """Flag *section* as having changed data on disk."""
    with _dirty_lock:
        _dirty_sections.add(section)


def drain_dirty() -> set[str]:
    """Return and clear the set of dirty sections (called by sync tick)."""
    with _dirty_lock:
        result = _dirty_sections.copy()
        _dirty_sections.clear()
    return result


# ---------------------------------------------------------------------------
# Async lock registry (one lock per file path)
# ---------------------------------------------------------------------------

_locks: dict[str, asyncio.Lock] = {}
_lock_mu = Lock()


def _get_lock(key: str) -> asyncio.Lock:
    with _lock_mu:
        if key not in _locks:
            _locks[key] = asyncio.Lock()
        return _locks[key]


# ---------------------------------------------------------------------------
# Low-level YAML helpers
# ---------------------------------------------------------------------------

def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


# ---------------------------------------------------------------------------
# Project reads/writes
# ---------------------------------------------------------------------------

def load_project(project_id: str) -> dict:
    return _read_yaml(PROJECTS_DIR / project_id / "project.yaml")


async def save_project_status(project_id: str, status: str, **extra) -> None:
    """Update the status field in project.yaml (single source of truth)."""
    path = PROJECTS_DIR / project_id / "project.yaml"
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        data["status"] = status
        data.update(extra)
        _write_yaml(path, data)
    mark_dirty("task_queue")
