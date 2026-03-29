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

from onemancompany.core.config import CONVERSATIONS_DIR_NAME, PROJECTS_DIR, EMPLOYEES_DIR, open_utf
from onemancompany.core.events import event_bus, CompanyEvent
from onemancompany.core.models import ConversationType, ConversationPhase, EventType

CONVERSATION_META_FILENAME = "meta.yaml"
CONVERSATION_MESSAGES_FILENAME = "messages.yaml"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Conversation:
    id: str
    type: str                    # ConversationType value
    phase: str                   # ConversationPhase value
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


def _release_lock(path: str) -> None:
    """Remove a lock from the cache (called when conversation closes)."""
    _locks.pop(path, None)


def resolve_conv_dir(conv: Conversation) -> Path:
    """Resolve conversation directory based on type and metadata."""
    if conv.type == ConversationType.CEO_INBOX:
        project_dir = conv.metadata.get("project_dir", "")
        return Path(project_dir) / CONVERSATIONS_DIR_NAME / conv.id
    else:  # oneonone
        return EMPLOYEES_DIR / conv.employee_id / CONVERSATIONS_DIR_NAME / conv.id


def save_conversation_meta(conv: Conversation) -> None:
    """Save conversation metadata to disk."""
    conv_dir = resolve_conv_dir(conv)
    conv_dir.mkdir(parents=True, exist_ok=True)
    meta_path = conv_dir / CONVERSATION_META_FILENAME
    logger.debug("[conversation] save meta: id={}, phase={}", conv.id, conv.phase)
    with open_utf(meta_path, "w") as f:
        yaml.dump(conv.to_dict(), f, allow_unicode=True)


def load_conversation_meta(conv_id: str, conv_dir: Path) -> Conversation:
    """Load conversation metadata from disk."""
    meta_path = conv_dir / CONVERSATION_META_FILENAME
    with open_utf(meta_path) as f:
        data = yaml.safe_load(f)
    return Conversation.from_dict(data)


async def append_message(conv_dir: Path, msg: Message) -> None:
    """Append a message to the conversation's messages.yaml."""
    conv_dir.mkdir(parents=True, exist_ok=True)
    msg_path = conv_dir / CONVERSATION_MESSAGES_FILENAME
    async with _get_lock(str(msg_path)):
        existing: list[dict] = []
        if msg_path.exists():
            with open_utf(msg_path) as f:
                existing = yaml.safe_load(f) or []
        existing.append(msg.to_dict())
        with open_utf(msg_path, "w") as f:
            yaml.dump(existing, f, allow_unicode=True)
    logger.debug("[conversation] appended message from {} in {}", msg.sender, conv_dir.name)


def load_messages(conv_dir: Path) -> list[Message]:
    """Load all messages from disk."""
    msg_path = conv_dir / CONVERSATION_MESSAGES_FILENAME
    if not msg_path.exists():
        return []
    with open_utf(msg_path) as f:
        data = yaml.safe_load(f) or []
    return [Message.from_dict(m) for m in data]


# ---------------------------------------------------------------------------
# ConversationService — lifecycle management
# ---------------------------------------------------------------------------


class ConversationService:
    """Manages conversation lifecycle. Stateless reads — always from disk."""

    def __init__(self) -> None:
        self._index: dict[str, Path] = {}

    def ensure_indexed(self, conv_id: str, conv_dir: Path) -> None:
        """Register a conversation directory in the in-memory index."""
        self._index[conv_id] = conv_dir

    async def create(
        self, type: str, employee_id: str, tools_enabled: bool = False, **metadata
    ) -> Conversation:
        conv_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conv = Conversation(
            id=conv_id, type=type, phase=ConversationPhase.ACTIVE.value,
            employee_id=employee_id, tools_enabled=tools_enabled,
            metadata=metadata, created_at=now,
        )
        save_conversation_meta(conv)
        await event_bus.publish(CompanyEvent(
            type=EventType.CONVERSATION_PHASE,
            payload={"conv_id": conv.id, "phase": conv.phase, "type": conv.type, "employee_id": conv.employee_id},
        ))
        conv_dir = resolve_conv_dir(conv)
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
                if conv.phase != ConversationPhase.ACTIVE:
                    continue
            elif conv.phase != phase:
                continue
            if type is not None and conv.type != type:
                continue
            result.append(conv)
        return result

    async def close(self, conv_id: str, wait_hooks: bool = False) -> tuple[Conversation, dict | None]:
        """Close a conversation. Returns (final_conversation, hook_result)."""
        conv = self.get(conv_id)
        conv.phase = ConversationPhase.CLOSING.value
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

        conv.phase = ConversationPhase.CLOSED.value
        conv.closed_at = datetime.now(timezone.utc).isoformat()
        save_conversation_meta(conv)
        await event_bus.publish(CompanyEvent(
            type=EventType.CONVERSATION_PHASE,
            payload={"conv_id": conv_id, "phase": conv.phase, "type": conv.type, "employee_id": conv.employee_id},
        ))

        # Clean up: remove from active index and release file lock
        conv_dir = self._index.pop(conv_id, None)
        if conv_dir:
            _release_lock(str(conv_dir / CONVERSATION_MESSAGES_FILENAME))

        logger.debug("[conversation] closed: id={}", conv_id)
        return conv, hook_result

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
            type=EventType.CONVERSATION_MESSAGE,
            payload={
                "conv_id": conv_id,
                "sender": msg.sender,
                "role": msg.role,
                "text": msg.text,
                "timestamp": msg.timestamp,
                "attachments": msg.attachments,
            },
        ))
        return msg

    def rebuild_index(self) -> None:
        """Rebuild in-memory index from disk on startup."""
        self._index.clear()
        if EMPLOYEES_DIR.exists():
            for emp_dir in EMPLOYEES_DIR.iterdir():
                conv_base = emp_dir / CONVERSATIONS_DIR_NAME
                if conv_base.exists():
                    for conv_dir in conv_base.iterdir():
                        meta = conv_dir / CONVERSATION_META_FILENAME
                        if meta.exists():
                            self._index[conv_dir.name] = conv_dir
        if PROJECTS_DIR.exists():
            for proj_dir in PROJECTS_DIR.iterdir():
                conv_base = proj_dir / CONVERSATIONS_DIR_NAME
                if conv_base.exists():
                    for conv_dir in conv_base.iterdir():
                        meta = conv_dir / CONVERSATION_META_FILENAME
                        if meta.exists():
                            self._index[conv_dir.name] = conv_dir
        logger.debug("[conversation] rebuilt index: {} conversations", len(self._index))

    async def recover(self) -> int:
        """Recover conversations stuck in 'closing' phase after a crash.

        Must be called AFTER rebuild_index(). Re-runs close hooks idempotently.
        Returns count of recovered conversations.
        """
        recovered = 0
        for conv_id in list(self._index):
            try:
                conv = self.get(conv_id)
            except Exception:
                logger.warning("[conversation] failed to load conversation {} during recovery", conv_id)
                continue
            if conv.phase == ConversationPhase.CLOSING:
                logger.info("[conversation] recovering stuck conversation: id={}", conv_id)
                try:
                    from onemancompany.core.conversation_hooks import run_close_hook
                    await run_close_hook(conv, wait=False)
                except ImportError:
                    logger.debug("[conversation] conversation_hooks not available during recovery")
                except Exception:
                    logger.exception("[conversation] recovery hook failed for {}", conv_id)
                # Finalize to closed
                conv.phase = ConversationPhase.CLOSED.value
                conv.closed_at = datetime.now(timezone.utc).isoformat()
                save_conversation_meta(conv)
                self._index.pop(conv_id, None)
                recovered += 1
        if recovered:
            logger.info("[conversation] recovered {} stuck conversation(s)", recovered)
        return recovered
