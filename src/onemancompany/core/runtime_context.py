"""Lightweight runtime context shared by tools and adapters.

Used to tag transient interaction metadata (e.g. one-on-one conversation
workdir) without mutating global process state.
"""

from __future__ import annotations

from contextvars import ContextVar

_interaction_type: ContextVar[str] = ContextVar("interaction_type", default="")
_interaction_work_dir: ContextVar[str] = ContextVar("interaction_work_dir", default="")


def get_interaction_type() -> str:
    return _interaction_type.get("")


def get_interaction_work_dir() -> str:
    return _interaction_work_dir.get("")
