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
