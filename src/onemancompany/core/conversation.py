"""Conversation data models and disk persistence.

Storage layout (SSOT — disk is the only truth):
- CEO inbox conversations:  {project_dir}/conversations/{conv_id}/
- 1-on-1 conversations:     {EMPLOYEES_DIR}/{emp_id}/conversations/{conv_id}/

Each conversation directory contains:
- meta.yaml   — conversation metadata (Conversation dataclass)
- messages.yaml — ordered list of messages (list[Message])
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml
from loguru import logger

from onemancompany.core.config import PROJECTS_DIR, EMPLOYEES_DIR


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Conversation:
    id: str
    type: str                    # "ceo_inbox" | "oneonone"
    phase: str                   # "created" | "active" | "closing" | "closed"
    employee_id: str
    tools_enabled: bool
    metadata: dict = field(default_factory=dict)
    created_at: str = ""
    closed_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Conversation:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Message:
    sender: str
    role: str
    text: str
    timestamp: str = ""
    attachments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------

# Per-file locks for concurrent write safety
_locks: dict[str, asyncio.Lock] = {}


def _get_lock(path: str) -> asyncio.Lock:
    if path not in _locks:
        _locks[path] = asyncio.Lock()
    return _locks[path]


def _resolve_conv_dir(conv: Conversation) -> Path:
    """Resolve conversation directory based on type and metadata."""
    if conv.type == "ceo_inbox":
        project_dir = conv.metadata.get("project_dir", "")
        return Path(project_dir) / "conversations" / conv.id
    else:  # oneonone
        return EMPLOYEES_DIR / conv.employee_id / "conversations" / conv.id


def save_conversation_meta(conv: Conversation) -> None:
    """Save conversation metadata to disk."""
    conv_dir = _resolve_conv_dir(conv)
    conv_dir.mkdir(parents=True, exist_ok=True)
    meta_path = conv_dir / "meta.yaml"
    logger.debug("[conversation] save meta: id={}, phase={}", conv.id, conv.phase)
    with open(meta_path, "w") as f:
        yaml.dump(conv.to_dict(), f, allow_unicode=True)


def load_conversation_meta(conv_id: str, conv_dir: Path) -> Conversation:
    """Load conversation metadata from disk."""
    meta_path = conv_dir / "meta.yaml"
    with open(meta_path) as f:
        data = yaml.safe_load(f)
    return Conversation.from_dict(data)


async def append_message(conv_dir: Path, msg: Message) -> None:
    """Append a message to the conversation's messages.yaml."""
    conv_dir.mkdir(parents=True, exist_ok=True)
    msg_path = conv_dir / "messages.yaml"
    async with _get_lock(str(msg_path)):
        existing: list[dict] = []
        if msg_path.exists():
            with open(msg_path) as f:
                existing = yaml.safe_load(f) or []
        existing.append(msg.to_dict())
        with open(msg_path, "w") as f:
            yaml.dump(existing, f, allow_unicode=True)
    logger.debug("[conversation] appended message from {} in {}", msg.sender, conv_dir.name)


def load_messages(conv_dir: Path) -> list[Message]:
    """Load all messages from disk."""
    msg_path = conv_dir / "messages.yaml"
    if not msg_path.exists():
        return []
    with open(msg_path) as f:
        data = yaml.safe_load(f) or []
    return [Message.from_dict(m) for m in data]
