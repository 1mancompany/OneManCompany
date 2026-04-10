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
from collections import deque
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

# Default timeout for EA auto-reply (seconds)
AUTO_REPLY_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Interaction:
    """A pending CEO interaction — blocks agent execution until resolved."""

    node_id: str
    tree_path: str
    project_id: str
    source_employee: str
    interaction_type: str      # ceo_request | project_confirm
    message: str
    future: asyncio.Future = field(repr=False)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class Conversation:
    id: str
    type: str                    # ConversationType value
    phase: str                   # ConversationPhase value
    employee_id: str
    tools_enabled: bool
    participants: list[str] = field(default_factory=list)
    project_id: str | None = None
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
    mentions: list[str] = field(default_factory=list)
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
        # In-memory pending interaction queue (not persisted — Futures can't serialize)
        self._pending: dict[str, deque[Interaction]] = {}   # conv_id → deque of Interaction
        self._auto_reply_tasks: dict[str, asyncio.Task] = {}  # "conv_id:node_id" → timer task
        # Locks for get_or_create_* to prevent duplicate conversation creation
        self._create_locks: dict[str, asyncio.Lock] = {}

    def ensure_indexed(self, conv_id: str, conv_dir: Path) -> None:
        """Register a conversation directory in the in-memory index."""
        self._index[conv_id] = conv_dir

    async def create(
        self, type: str, employee_id: str, tools_enabled: bool = False,
        participants: list[str] | None = None,
        project_id: str | None = None,
        **metadata,
    ) -> Conversation:
        conv_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conv = Conversation(
            id=conv_id, type=type, phase=ConversationPhase.ACTIVE.value,
            employee_id=employee_id, tools_enabled=tools_enabled,
            participants=participants or [],
            project_id=project_id,
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

        # Drain pending interactions — reject Futures so blocked agents unblock
        self._drain_pending(conv_id)

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
        self, conv_id: str, sender: str, role: str, text: str,
        attachments: list[str] | None = None, mentions: list[str] | None = None,
        _broadcast: bool = True,
    ) -> Message:
        """Persist a message (CEO or agent). Does NOT dispatch to adapter — caller handles that.

        Args:
            _broadcast: If False, skip the CONVERSATION_MESSAGE event. Used by
                push_system_message() which publishes its own richer event.
        """
        conv_dir = self._index.get(conv_id)
        if not conv_dir:
            raise ValueError(f"Conversation {conv_id} not found")
        now = datetime.now(timezone.utc).isoformat()
        msg = Message(
            sender=sender, role=role, text=text,
            timestamp=now, mentions=mentions or [], attachments=attachments or [],
        )
        await append_message(conv_dir, msg)
        if _broadcast:
            await event_bus.publish(CompanyEvent(
                type=EventType.CONVERSATION_MESSAGE,
                payload={
                    "conv_id": conv_id,
                    "sender": msg.sender,
                    "role": msg.role,
                    "text": msg.text,
                    "timestamp": msg.timestamp,
                    "mentions": msg.mentions,
                    "attachments": msg.attachments,
                },
            ))
        return msg

    # ------------------------------------------------------------------
    # Pending interaction queue (in-memory)
    # ------------------------------------------------------------------

    async def enqueue_interaction(self, conv_id: str, interaction: Interaction) -> None:
        """Add a pending interaction to a conversation's queue."""
        if conv_id not in self._pending:
            self._pending[conv_id] = deque()
        self._pending[conv_id].append(interaction)
        # Push the interaction message to conversation history
        await self.send_message(
            conv_id, sender=interaction.source_employee,
            role="system", text=interaction.message,
        )
        logger.debug(
            "[conversation] enqueued interaction conv_id={} node_id={} type={}",
            conv_id, interaction.node_id, interaction.interaction_type,
        )
        # Start auto-reply timer
        self._start_auto_reply_timer(conv_id, interaction)

    async def resolve_interaction(self, conv_id: str, ceo_text: str) -> dict:
        """CEO replies — resolve the oldest pending interaction."""
        pending = self._pending.get(conv_id, deque())
        if not pending:
            return {"type": "followup", "text": ceo_text}

        interaction = pending.popleft()
        # Cancel auto-reply timer
        timer_key = f"{conv_id}:{interaction.node_id}"
        timer = self._auto_reply_tasks.pop(timer_key, None)
        if timer and not timer.done():
            timer.cancel()
        # Resolve the Future so the blocked agent continues
        if not interaction.future.done():
            interaction.future.set_result(ceo_text)

        logger.info(
            "[conversation] resolved interaction conv_id={} node_id={} type={}",
            conv_id, interaction.node_id, interaction.interaction_type,
        )
        return {"type": "resolved", "node_id": interaction.node_id}

    def get_pending_count(self, conv_id: str) -> int:
        """Return the number of unresolved pending interactions for a conversation."""
        pending = self._pending.get(conv_id, deque())
        return len([p for p in pending if not p.future.done()])

    def _start_auto_reply_timer(self, conv_id: str, interaction: Interaction) -> None:
        """Start timer. If CEO doesn't respond within timeout, EA auto-replies.

        When CEO DND mode is on, auto-reply triggers immediately (0s timeout).
        """
        from onemancompany.core.config import get_ceo_dnd
        timeout = 0 if get_ceo_dnd() else AUTO_REPLY_TIMEOUT

        async def _timer() -> None:
            try:
                await asyncio.sleep(timeout)
                if not interaction.future.done():
                    reply = await self._ea_auto_reply(conv_id, interaction)
                    if not interaction.future.done():
                        interaction.future.set_result(reply)
                        # Remove from pending queue
                        pending = self._pending.get(conv_id, deque())
                        try:
                            pending.remove(interaction)
                        except ValueError:
                            logger.debug("[conversation] interaction already removed from pending queue")
                        logger.info(
                            "[conversation] EA auto-replied conv_id={} node_id={}",
                            conv_id, interaction.node_id,
                        )
            except asyncio.CancelledError:
                logger.debug(
                    "[conversation] auto-reply timer cancelled conv_id={} node_id={}",
                    conv_id, interaction.node_id,
                )
            except Exception as e:
                logger.error(
                    "[conversation] auto-reply error conv_id={} node_id={}: {}",
                    conv_id, interaction.node_id, e,
                )
            finally:
                self._auto_reply_tasks.pop(f"{conv_id}:{interaction.node_id}", None)

        timer_key = f"{conv_id}:{interaction.node_id}"
        try:
            task = asyncio.create_task(_timer())
        except RuntimeError:
            # No running event loop (e.g. in tests) — skip timer
            logger.debug("[conversation] no event loop, skipping auto-reply timer for node={}", interaction.node_id)
            return
        self._auto_reply_tasks[timer_key] = task

    @staticmethod
    async def _ea_auto_reply(conv_id: str, interaction: Interaction) -> str:
        """EA reads the request and decides accept/reject on behalf of CEO.

        EA reads the request and auto-replies on behalf of CEO.
        """
        import json
        import re

        from onemancompany.agents.base import _extract_text, make_llm, tracked_ainvoke
        from onemancompany.core.config import EA_ID

        llm = make_llm(EA_ID)
        prompt = (
            "You are the EA (Executive Assistant), making a decision on behalf of the CEO.\n\n"
            "An employee has sent the following request to the CEO inbox:\n"
            f"---\n{interaction.message}\n---\n\n"
            "The CEO has not responded within the timeout period. "
            "You need to make a decision: accept or reject this request, with a brief reason.\n\n"
            "Guidelines:\n"
            "- Accept requests that are reasonable, well-scoped, and align with business goals\n"
            "- Reject requests that are vague, out of scope, or need more information\n"
            "- Keep your response concise (2-3 sentences)\n\n"
            "Return your decision in JSON format:\n"
            '{"decision": "accept" or "reject", "reason": "your brief explanation"}\n'
            "Only return JSON, no other content."
        )

        try:
            resp = await asyncio.wait_for(
                tracked_ainvoke(llm, prompt, category="ea_auto_reply", employee_id=EA_ID),
                timeout=60,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[ea_auto_reply] LLM timed out for conv={} node={}, defaulting to accept",
                conv_id, interaction.node_id,
            )
            return "[EA Auto-Reply] Decision: ACCEPT\nAuto-approved (EA LLM call timed out)"

        raw = _extract_text(resp.content)
        decision = "accept"
        reason = "EA auto-approved (no valid response parsed)"
        try:
            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                decision = parsed.get("decision", "accept")
                reason = parsed.get("reason", "")
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.debug("[ea_auto_reply] failed to parse EA response: {}", exc)

        reply_text = f"[EA Auto-Reply] Decision: {decision.upper()}\n{reason}"
        logger.info("[ea_auto_reply] conv={} node={} decision={}", conv_id, interaction.node_id, decision)
        return reply_text

    # ------------------------------------------------------------------
    # High-level helpers (project conversations, 1-on-1, reactivation)
    # ------------------------------------------------------------------

    def _get_create_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for get_or_create_* dedup."""
        if key not in self._create_locks:
            self._create_locks[key] = asyncio.Lock()
        return self._create_locks[key]

    async def get_or_create_project_conversation(
        self, project_id: str, participants: list[str] | None = None,
    ) -> Conversation:
        """Get existing project conversation or create new one."""
        async with self._get_create_lock(f"project:{project_id}"):
            for conv in self.list_by_phase(type=ConversationType.PROJECT.value):
                if conv.project_id == project_id:
                    if participants:
                        changed = False
                        for p in participants:
                            if p not in conv.participants:
                                conv.participants.append(p)
                                changed = True
                        if changed:
                            save_conversation_meta(conv)
                    return conv
            return await self.create(
                type=ConversationType.PROJECT.value,
                employee_id=participants[0] if participants else "",
                participants=participants or [],
                project_id=project_id,
            )

    async def get_or_create_oneonone(self, employee_id: str) -> Conversation:
        """Get existing 1-on-1 or create one."""
        async with self._get_create_lock(f"oneonone:{employee_id}"):
            for conv in self.list_by_phase(type=ConversationType.ONE_ON_ONE.value):
                if conv.employee_id == employee_id and conv.phase != ConversationPhase.CLOSED.value:
                    return conv
            return await self.create(
                type=ConversationType.ONE_ON_ONE.value,
                employee_id=employee_id,
                participants=[employee_id],
            )

    async def push_system_message(
        self, conv_id: str, message: str, source_employee: str = "",
    ) -> Message:
        """Push a system message and broadcast CONVERSATION_MESSAGE event."""
        msg = await self.send_message(conv_id, "system", source_employee or "system", message, _broadcast=False)
        conv = self.get(conv_id)
        await event_bus.publish(CompanyEvent(
            type=EventType.CONVERSATION_MESSAGE,
            payload={
                "conv_id": conv_id,
                "type": conv.type,
                "sender": "system",
                "text": message,
                "source_employee": source_employee,
                "project_id": conv.project_id or "",
                "employee_id": conv.employee_id,
            },
            agent="SYSTEM",
        ))
        return msg

    async def reactivate(self, conv_id: str) -> Conversation:
        """Reactivate an archived conversation (archived -> active)."""
        conv = self.get(conv_id)
        if conv.phase == ConversationPhase.ARCHIVED.value:
            conv.phase = ConversationPhase.ACTIVE.value
            conv.closed_at = None
            save_conversation_meta(conv)
            logger.debug("[conversation] reactivated: id={}", conv_id)
        return conv

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

    def _drain_pending(self, conv_id: str) -> int:
        """Cancel auto-reply timers and reject all pending Futures for a conversation.

        Returns the number of drained interactions.
        """
        pending = self._pending.pop(conv_id, deque())
        drained = 0
        for interaction in pending:
            # Cancel associated timer
            timer_key = f"{conv_id}:{interaction.node_id}"
            timer = self._auto_reply_tasks.pop(timer_key, None)
            if timer and not timer.done():
                timer.cancel()
            # Reject Future so blocked agent unblocks with an error
            if not interaction.future.done():
                interaction.future.set_exception(
                    RuntimeError(f"Conversation {conv_id} closed while interaction pending")
                )
            drained += 1
        if drained:
            logger.info("[conversation] drained {} pending interaction(s) for conv_id={}", drained, conv_id)
        return drained

    def cancel_all_timers(self) -> None:
        """Cancel all auto-reply timer tasks. Called during server shutdown."""
        for key, task in list(self._auto_reply_tasks.items()):
            if not task.done():
                task.cancel()
        self._auto_reply_tasks.clear()
        logger.debug("[conversation] cancelled all auto-reply timers")

    def remove_by_project(self, project_id: str) -> int:
        """Remove all conversations for a project from the in-memory index.

        Returns the count of removed conversations.  Disk files are cleaned up
        by the caller (e.g. ``shutil.rmtree`` on the project directory).
        """
        base_pid = project_id.split("/")[0]
        to_remove = []
        for conv_id, conv_dir in self._index.items():
            try:
                conv = load_conversation_meta(conv_id, conv_dir)
            except Exception as exc:
                logger.debug("[conversation] failed to load meta for {} during remove_by_project: {}", conv_id, exc)
                continue
            if conv.project_id and conv.project_id.split("/")[0] == base_pid:
                to_remove.append(conv_id)
        for conv_id in to_remove:
            self._index.pop(conv_id, None)
        if to_remove:
            logger.debug("[conversation] removed {} conversations for project {}", len(to_remove), project_id)
        return len(to_remove)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_service_instance: ConversationService | None = None


def get_conversation_service() -> ConversationService:
    """Return the module-level ConversationService singleton (lazy-init)."""
    global _service_instance
    if _service_instance is None:
        _service_instance = ConversationService()
    return _service_instance
