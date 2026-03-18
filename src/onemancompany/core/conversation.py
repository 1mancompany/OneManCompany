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
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from loguru import logger

from onemancompany.core.config import PROJECTS_DIR, EMPLOYEES_DIR
from onemancompany.core.events import event_bus, CompanyEvent


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


# ---------------------------------------------------------------------------
# ConversationService — lifecycle management
# ---------------------------------------------------------------------------


class ConversationService:
    """Manages conversation lifecycle. Stateless reads — always from disk."""

    def __init__(self) -> None:
        self._index: dict[str, Path] = {}

    async def create(
        self, type: str, employee_id: str, tools_enabled: bool = False, **metadata
    ) -> Conversation:
        conv_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conv = Conversation(
            id=conv_id, type=type, phase="active",
            employee_id=employee_id, tools_enabled=tools_enabled,
            metadata=metadata, created_at=now,
        )
        save_conversation_meta(conv)
        await event_bus.publish(CompanyEvent(
            type="conversation_phase",
            payload={"conv_id": conv.id, "phase": conv.phase, "type": conv.type, "employee_id": conv.employee_id},
        ))
        conv_dir = _resolve_conv_dir(conv)
        self._index[conv_id] = conv_dir
        logger.debug("[conversation] created: id={}, type={}, employee={}", conv_id, type, employee_id)
        return conv

    def get(self, conv_id: str) -> Conversation:
        conv_dir = self._index.get(conv_id)
        if not conv_dir:
            raise ValueError(f"Conversation {conv_id} not found")
        return load_conversation_meta(conv_id, conv_dir)

    def get_messages(self, conv_id: str) -> list[Message]:
        conv_dir = self._index.get(conv_id)
        if not conv_dir:
            raise ValueError(f"Conversation {conv_id} not found")
        return load_messages(conv_dir)

    def list_active(self, type: str | None = None) -> list[Conversation]:
        return self.list_by_phase(type=type, phase=None)

    def list_by_phase(self, type: str | None = None, phase: str | None = None) -> list[Conversation]:
        result = []
        for conv_id, conv_dir in self._index.items():
            try:
                conv = load_conversation_meta(conv_id, conv_dir)
            except Exception:
                logger.warning("[conversation] failed to load meta for {}", conv_id)
                continue
            if phase is None:
                if conv.phase not in ("active", "created"):
                    continue
            elif conv.phase != phase:
                continue
            if type is not None and conv.type != type:
                continue
            result.append(conv)
        return result

    async def close(self, conv_id: str, wait_hooks: bool = False) -> dict | None:
        conv = self.get(conv_id)
        conv.phase = "closing"
        save_conversation_meta(conv)
        logger.debug("[conversation] closing: id={}", conv_id)

        # Run close hooks (imported lazily to avoid circular deps)
        # conversation_hooks.py may not exist yet — handle gracefully
        hook_result = None
        try:
            from onemancompany.core.conversation_hooks import run_close_hook
            hook_result = await run_close_hook(conv, wait=wait_hooks)
        except ImportError:
            logger.debug("[conversation] conversation_hooks not yet available, skipping close hook")
        except Exception:
            logger.exception("[conversation] close hook failed for {}", conv_id)

        conv.phase = "closed"
        conv.closed_at = datetime.now(timezone.utc).isoformat()
        save_conversation_meta(conv)
        await event_bus.publish(CompanyEvent(
            type="conversation_phase",
            payload={"conv_id": conv_id, "phase": conv.phase, "type": conv.type, "employee_id": conv.employee_id},
        ))
        logger.debug("[conversation] closed: id={}", conv_id)
        return hook_result

    async def send_message(
        self, conv_id: str, sender: str, role: str, text: str, attachments: list[str] | None = None,
    ) -> Message:
        """Persist a message (CEO or agent). Does NOT dispatch to adapter — caller handles that."""
        conv_dir = self._index.get(conv_id)
        if not conv_dir:
            raise ValueError(f"Conversation {conv_id} not found")
        now = datetime.now(timezone.utc).isoformat()
        msg = Message(
            sender=sender, role=role, text=text,
            timestamp=now, attachments=attachments or [],
        )
        await append_message(conv_dir, msg)
        await event_bus.publish(CompanyEvent(
            type="conversation_message",
            payload={
                "conv_id": conv_id,
                "sender": msg.sender,
                "role": msg.role,
                "text": msg.text,
                "timestamp": msg.timestamp,
            },
        ))
        return msg

    def rebuild_index(self) -> None:
        """Rebuild in-memory index from disk on startup."""
        self._index.clear()
        if EMPLOYEES_DIR.exists():
            for emp_dir in EMPLOYEES_DIR.iterdir():
                conv_base = emp_dir / "conversations"
                if conv_base.exists():
                    for conv_dir in conv_base.iterdir():
                        meta = conv_dir / "meta.yaml"
                        if meta.exists():
                            self._index[conv_dir.name] = conv_dir
        if PROJECTS_DIR.exists():
            for proj_dir in PROJECTS_DIR.iterdir():
                conv_base = proj_dir / "conversations"
                if conv_base.exists():
                    for conv_dir in conv_base.iterdir():
                        meta = conv_dir / "meta.yaml"
                        if meta.exists():
                            self._index[conv_dir.name] = conv_dir
        logger.debug("[conversation] rebuilt index: {} conversations", len(self._index))
