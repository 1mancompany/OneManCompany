# Unified Conversation Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify CEO inbox and 1-on-1 conversation systems into a single backend ConversationService + frontend ChatPanel, with adapter-based executor dispatch.

**Architecture:** A thin ConversationService layer on top of existing executors. Registry-based adapters translate between the unified conversation protocol and executor-specific implementations (LangChain / Claude session). Frontend replaces two modal dialogs with a single ChatPanel embedded in the right panel.

**Tech Stack:** Python 3.11+, FastAPI, loguru, YAML persistence, vanilla JS + Canvas 2D

**Spec:** `docs/superpowers/specs/2026-03-18-unified-conversation-module-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/onemancompany/core/conversation.py` | Data models (Conversation, Message), ConversationService, disk I/O |
| `src/onemancompany/core/conversation_adapters.py` | Adapter protocol, LangChainAdapter, ClaudeSessionAdapter, adapter registry |
| `src/onemancompany/core/conversation_hooks.py` | Close hooks registry, ceo_inbox hook, oneonone hook |
| `tests/unit/core/test_conversation.py` | Unit tests for ConversationService |
| `tests/unit/core/test_conversation_adapters.py` | Unit tests for adapters |
| `tests/unit/core/test_conversation_hooks.py` | Unit tests for close hooks |
| `tests/integration/test_conversation_api.py` | Integration tests for unified REST API |
| `frontend/conversation.js` | ChatPanel class + right panel conversation integration |

### Modified Files
| File | Changes |
|------|---------|
| `src/onemancompany/core/events.py` | Add `conversation_message`, `conversation_phase` to EventType |
| `src/onemancompany/api/routes.py` | Add unified `/api/conversation/*` endpoints, rewire legacy endpoints |
| `frontend/app.js` | RightPanel routing, WebSocket handler for new events, import ChatPanel |
| `frontend/index.html` | Right panel structure changes, keep legacy modals during transition |
| `frontend/style.css` | ChatPanel styles |

---

## Task 1: Data Models + Disk Persistence

**Files:**
- Create: `src/onemancompany/core/conversation.py`
- Test: `tests/unit/core/test_conversation.py`

**Context:** The data models are the foundation. `Conversation` and `Message` dataclasses with YAML serialization. Storage paths follow SSOT: inbox conversations under `{project_dir}/conversations/`, 1-on-1 under `{EMPLOYEES_DIR}/{emp_id}/conversations/`. All reads go to disk, no caching.

- [ ] **Step 1: Write failing tests for data models**

```python
# tests/unit/core/test_conversation.py
import pytest
from onemancompany.core.conversation import Conversation, Message

def test_conversation_to_dict():
    conv = Conversation(
        id="test-uuid",
        type="ceo_inbox",
        phase="created",
        employee_id="00100",
        tools_enabled=False,
        metadata={"node_id": "node-1"},
        created_at="2026-03-18T10:00:00",
        closed_at=None,
    )
    d = conv.to_dict()
    assert d["id"] == "test-uuid"
    assert d["type"] == "ceo_inbox"
    assert d["phase"] == "created"
    assert d["tools_enabled"] is False

def test_conversation_from_dict():
    d = {
        "id": "test-uuid", "type": "oneonone", "phase": "active",
        "employee_id": "00100", "tools_enabled": True,
        "metadata": {}, "created_at": "2026-03-18T10:00:00", "closed_at": None,
    }
    conv = Conversation.from_dict(d)
    assert conv.type == "oneonone"
    assert conv.tools_enabled is True

def test_message_to_dict():
    msg = Message(sender="ceo", role="CEO", text="hello", timestamp="2026-03-18T10:00:00", attachments=[])
    d = msg.to_dict()
    assert d["sender"] == "ceo"
    assert d["text"] == "hello"

def test_message_from_dict():
    d = {"sender": "00100", "role": "Alice", "text": "hi", "timestamp": "2026-03-18T10:00:00", "attachments": ["/tmp/f.txt"]}
    msg = Message.from_dict(d)
    assert msg.attachments == ["/tmp/f.txt"]
```

- [ ] **Step 2: Run tests — expect FAIL (module not found)**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation.py -x -v`

- [ ] **Step 3: Implement data models**

```python
# src/onemancompany/core/conversation.py
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation.py -x -v`

- [ ] **Step 5: Write failing tests for disk persistence**

```python
# tests/unit/core/test_conversation.py (append)
import yaml

from onemancompany.core.conversation import (
    save_conversation_meta, load_conversation_meta,
    append_message, load_messages,
    _resolve_conv_dir,
)


def test_save_and_load_meta(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    conv = Conversation(
        id="conv-001", type="ceo_inbox", phase="active",
        employee_id="00100", tools_enabled=False,
        metadata={"node_id": "n1", "project_dir": str(tmp_path / "projects" / "proj1")},
        created_at="2026-03-18T10:00:00",
    )
    save_conversation_meta(conv)
    loaded = load_conversation_meta("conv-001", conv_dir=_resolve_conv_dir(conv))
    assert loaded.id == "conv-001"
    assert loaded.phase == "active"


@pytest.mark.asyncio
async def test_append_and_load_messages(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    conv = Conversation(
        id="conv-002", type="oneonone", phase="active",
        employee_id="00100", tools_enabled=True,
        metadata={},
        created_at="2026-03-18T10:00:00",
    )
    conv_dir = tmp_path / "employees" / "00100" / "conversations" / "conv-002"
    save_conversation_meta(conv)

    msg1 = Message(sender="ceo", role="CEO", text="hello", timestamp="2026-03-18T10:00:01")
    msg2 = Message(sender="00100", role="Alice", text="hi back", timestamp="2026-03-18T10:00:02")
    await append_message(conv_dir, msg1)
    await append_message(conv_dir, msg2)

    messages = load_messages(conv_dir)
    assert len(messages) == 2
    assert messages[0].sender == "ceo"
    assert messages[1].text == "hi back"
```

- [ ] **Step 6: Run tests — expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation.py -x -v`

- [ ] **Step 7: Implement disk persistence functions**

Add to `src/onemancompany/core/conversation.py`:

```python
import asyncio
from pathlib import Path

import yaml
from loguru import logger

from onemancompany.core.config import PROJECTS_DIR, EMPLOYEES_DIR

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
        existing = []
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
```

- [ ] **Step 8: Run tests — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation.py -x -v`

- [ ] **Step 9: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.core.conversation import Conversation, Message, ConversationService; print('OK')"`

(This will fail — ConversationService not yet implemented. That's expected. Just verify import of data models works.)

Run: `.venv/bin/python -c "from onemancompany.core.conversation import Conversation, Message; print('OK')"`

- [ ] **Step 10: Commit**

```bash
git add src/onemancompany/core/conversation.py tests/unit/core/test_conversation.py
git commit -m "feat(conversation): add data models and disk persistence"
```

---

## Task 2: ConversationService Core

**Files:**
- Modify: `src/onemancompany/core/conversation.py`
- Test: `tests/unit/core/test_conversation.py`

**Context:** The service manages lifecycle and delegates to adapters. Stateless reads (always from disk). `send_message` is async — persists CEO message, dispatches to adapter in background, persists reply when it arrives. For now, adapter dispatch is stubbed — we'll implement real adapters in Task 4-5.

- [ ] **Step 1: Write failing tests for ConversationService**

```python
# tests/unit/core/test_conversation.py (append)
from unittest.mock import AsyncMock
from onemancompany.core.conversation import ConversationService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    return ConversationService()


@pytest.mark.asyncio
async def test_create_conversation(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    conv = await svc.create(
        type="oneonone", employee_id="00100", tools_enabled=True,
    )
    assert conv.phase == "active"
    assert conv.type == "oneonone"
    assert conv.employee_id == "00100"
    # Verify persisted to disk
    loaded = svc.get(conv.id)
    assert loaded.id == conv.id


@pytest.mark.asyncio
async def test_close_conversation(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    conv = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)
    result = await svc.close(conv.id, wait_hooks=False)
    closed = svc.get(conv.id)
    assert closed.phase == "closed"


@pytest.mark.asyncio
async def test_list_active(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    c1 = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)
    c2 = await svc.create(type="oneonone", employee_id="00101", tools_enabled=True)
    await svc.close(c2.id)
    active = svc.list_active()
    assert len(active) == 1
    assert active[0].id == c1.id


@pytest.mark.asyncio
async def test_list_active_filter_by_type(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    c1 = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)
    c2 = await svc.create(
        type="ceo_inbox", employee_id="00100", tools_enabled=False,
        project_dir=str(tmp_path / "projects" / "p1"), node_id="n1",
    )
    active = svc.list_active(type="ceo_inbox")
    assert len(active) == 1
    assert active[0].type == "ceo_inbox"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation.py::test_create_conversation -x -v`

- [ ] **Step 3: Implement ConversationService**

Add to `src/onemancompany/core/conversation.py`:

```python
import uuid

class ConversationService:
    """Manages conversation lifecycle. Stateless reads — always from disk."""

    def __init__(self) -> None:
        # In-memory index: conv_id -> conv_dir (for fast lookup without full scan)
        # Rebuilt on startup from disk scan
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
                continue
            # Default: return active/created only
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
        from onemancompany.core.conversation_hooks import run_close_hook
        hook_result = None
        try:
            hook_result = await run_close_hook(conv, wait=wait_hooks)
        except Exception:
            logger.exception("[conversation] close hook failed for {}", conv_id)

        conv.phase = "closed"
        conv.closed_at = datetime.now(timezone.utc).isoformat()
        save_conversation_meta(conv)
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
        return msg

    def rebuild_index(self) -> None:
        """Rebuild in-memory index from disk on startup."""
        self._index.clear()
        # Scan employee conversation dirs
        if EMPLOYEES_DIR.exists():
            for emp_dir in EMPLOYEES_DIR.iterdir():
                conv_base = emp_dir / "conversations"
                if conv_base.exists():
                    for conv_dir in conv_base.iterdir():
                        meta = conv_dir / "meta.yaml"
                        if meta.exists():
                            self._index[conv_dir.name] = conv_dir
        # Scan project conversation dirs
        if PROJECTS_DIR.exists():
            for proj_dir in PROJECTS_DIR.iterdir():
                conv_base = proj_dir / "conversations"
                if conv_base.exists():
                    for conv_dir in conv_base.iterdir():
                        meta = conv_dir / "meta.yaml"
                        if meta.exists():
                            self._index[conv_dir.name] = conv_dir
        logger.debug("[conversation] rebuilt index: {} conversations", len(self._index))
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation.py -x -v`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/conversation.py tests/unit/core/test_conversation.py
git commit -m "feat(conversation): add ConversationService with lifecycle management"
```

---

## Task 3: Adapter Protocol + Registry

**Files:**
- Create: `src/onemancompany/core/conversation_adapters.py`
- Test: `tests/unit/core/test_conversation_adapters.py`

**Context:** The adapter protocol defines how conversations interact with different executor types. Registry-based dispatch. For this task, implement the protocol, registry, and a mock adapter for testing. Real adapter implementations come in Tasks 4-5.

- [ ] **Step 1: Write failing tests for adapter registry**

```python
# tests/unit/core/test_conversation_adapters.py
import pytest
from onemancompany.core.conversation import Conversation, Message
from onemancompany.core.conversation_adapters import (
    register_adapter, get_adapter, ConversationAdapter,
)


def test_register_and_get_adapter():
    @register_adapter("test_executor")
    class TestAdapter:
        async def send(self, conversation, messages, new_message):
            return "test reply"
        async def on_create(self, conversation):
            pass
        async def on_close(self, conversation):
            pass

    adapter = get_adapter("test_executor")
    assert adapter is not None


def test_get_unknown_adapter_raises():
    with pytest.raises(KeyError):
        get_adapter("nonexistent_executor_type")


@pytest.mark.asyncio
async def test_adapter_send():
    @register_adapter("echo_executor")
    class EchoAdapter:
        async def send(self, conversation, messages, new_message):
            return f"echo: {new_message.text}"
        async def on_create(self, conversation):
            pass
        async def on_close(self, conversation):
            pass

    adapter = get_adapter("echo_executor")()
    conv = Conversation(
        id="c1", type="oneonone", phase="active",
        employee_id="00100", tools_enabled=True,
        created_at="2026-03-18T10:00:00",
    )
    msg = Message(sender="ceo", role="CEO", text="hello", timestamp="2026-03-18T10:00:01")
    reply = await adapter.send(conv, [], msg)
    assert reply == "echo: hello"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation_adapters.py -x -v`

- [ ] **Step 3: Implement adapter protocol and registry**

```python
# src/onemancompany/core/conversation_adapters.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

from loguru import logger

from onemancompany.core.conversation import Conversation, Message


@runtime_checkable
class ConversationAdapter(Protocol):
    async def send(
        self, conversation: Conversation, messages: list[Message], new_message: Message,
    ) -> str:
        """Send message with full history, return agent reply text."""
        ...

    async def on_create(self, conversation: Conversation) -> None:
        """Optional init when conversation starts."""
        ...

    async def on_close(self, conversation: Conversation) -> None:
        """Optional adapter-level cleanup (release resources)."""
        ...


_adapter_registry: dict[str, type] = {}


def register_adapter(executor_type: str):
    """Decorator to register an adapter class for an executor type."""
    def decorator(cls):
        _adapter_registry[executor_type] = cls
        logger.debug("[conversation] registered adapter: {}", executor_type)
        return cls
    return decorator


def get_adapter(executor_type: str) -> type:
    """Get adapter class by executor type. Raises KeyError if not found."""
    if executor_type not in _adapter_registry:
        raise KeyError(f"No conversation adapter for executor type: {executor_type}")
    return _adapter_registry[executor_type]
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation_adapters.py -x -v`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/conversation_adapters.py tests/unit/core/test_conversation_adapters.py
git commit -m "feat(conversation): add adapter protocol and registry"
```

---

## Task 4: LangChainAdapter Implementation

**Files:**
- Modify: `src/onemancompany/core/conversation_adapters.py`
- Test: `tests/unit/core/test_conversation_adapters.py`

**Context:** The LangChainAdapter wraps `executor.execute()` for company-hosted employees. It builds conversation context from message history and delegates to the existing executor pipeline. Reference: current 1-on-1 implementation at `routes.py:807-926` and CEO inbox implementation at `ceo_conversation.py:72-152`.

- [ ] **Step 1: Write failing test for LangChainAdapter**

```python
# tests/unit/core/test_conversation_adapters.py (append)
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_langchain_adapter_send():
    from onemancompany.core.conversation_adapters import LangChainAdapter

    conv = Conversation(
        id="c1", type="oneonone", phase="active",
        employee_id="00100", tools_enabled=True,
        created_at="2026-03-18T10:00:00",
    )
    history = [
        Message(sender="ceo", role="CEO", text="what's your status?", timestamp="t1"),
        Message(sender="00100", role="Alice", text="working on task X", timestamp="t2"),
    ]
    new_msg = Message(sender="ceo", role="CEO", text="tell me more", timestamp="t3")

    # Mock executor returns LaunchResult with .output attribute
    mock_result = MagicMock()
    mock_result.output = "Here are the details about task X..."
    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=mock_result)

    with patch(
        "onemancompany.core.conversation_adapters._get_employee_executor",
        return_value=mock_executor,
    ):
        adapter = LangChainAdapter()
        reply = await adapter.send(conv, history, new_msg)

    assert reply == "Here are the details about task X..."
    mock_executor.execute.assert_awaited_once()
    # Verify history was passed in the prompt (first positional arg)
    call_args = mock_executor.execute.call_args
    prompt = call_args[0][0]
    assert "what's your status?" in prompt or "tell me more" in prompt
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation_adapters.py::test_langchain_adapter_send -x -v`

- [ ] **Step 3: Implement LangChainAdapter**

Add to `src/onemancompany/core/conversation_adapters.py`:

```python
def _get_employee_executor(employee_id: str):
    """Get the Launcher for an employee. Lazy import to avoid circular deps."""
    from onemancompany.core.vessel import employee_manager
    executor = employee_manager.executors.get(employee_id)
    if not executor:
        raise ValueError(f"No executor for employee {employee_id}")
    return executor


def _get_executor_type(employee_id: str) -> str:
    """Determine executor type string from Launcher subclass."""
    executor = _get_employee_executor(employee_id)
    cls_name = type(executor).__name__
    if "ClaudeSession" in cls_name:
        return "claude_session"
    return "langchain"


def _build_conversation_prompt(
    conversation: Conversation, messages: list[Message], new_message: Message,
) -> str:
    """Build a prompt with conversation history for the executor."""
    lines = []
    lines.append("You are in a conversation with the CEO.")
    if conversation.type == "oneonone":
        lines.append("This is a 1-on-1 meeting. Be direct and professional.")
    elif conversation.type == "ceo_inbox":
        lines.append("The CEO is responding to your request. Answer their questions.")

    if messages:
        lines.append("\n--- Conversation History ---")
        for msg in messages:
            lines.append(f"[{msg.role}]: {msg.text}")

    lines.append(f"\n[{new_message.role}]: {new_message.text}")
    lines.append("\nPlease respond:")
    return "\n".join(lines)


@register_adapter("langchain")
class LangChainAdapter:
    async def send(
        self, conversation: Conversation, messages: list[Message], new_message: Message,
    ) -> str:
        executor = _get_employee_executor(conversation.employee_id)
        prompt = _build_conversation_prompt(conversation, messages, new_message)
        logger.debug(
            "[conversation] LangChainAdapter.send: employee={}, tools={}",
            conversation.employee_id, conversation.tools_enabled,
        )
        from onemancompany.core.vessel import TaskContext
        ctx = TaskContext(
            employee_id=conversation.employee_id,
            project_id=conversation.metadata.get("project_id", ""),
        )
        result = await executor.execute(prompt, ctx)
        return result.output

    async def on_create(self, conversation: Conversation) -> None:
        pass

    async def on_close(self, conversation: Conversation) -> None:
        pass
```

- [ ] **Step 4: Run test — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation_adapters.py::test_langchain_adapter_send -x -v`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/conversation_adapters.py tests/unit/core/test_conversation_adapters.py
git commit -m "feat(conversation): implement LangChainAdapter"
```

---

## Task 5: ClaudeSessionAdapter Implementation

**Files:**
- Modify: `src/onemancompany/core/conversation_adapters.py`
- Test: `tests/unit/core/test_conversation_adapters.py`

**Context:** The ClaudeSessionAdapter wraps `executor.execute()` for self-hosted employees. Same pattern as LangChainAdapter but routed through `ClaudeSessionExecutor`. The daemon is single-turn (full history injected per call). Lock is per-employee-per-project via `_get_session_lock()`. Reference: `claude_session.py:40-44`, `vessel.py` ClaudeSessionExecutor.

- [ ] **Step 1: Write failing test for ClaudeSessionAdapter**

```python
# tests/unit/core/test_conversation_adapters.py (append)

@pytest.mark.asyncio
async def test_claude_session_adapter_send():
    from onemancompany.core.conversation_adapters import ClaudeSessionAdapter

    conv = Conversation(
        id="c1", type="oneonone", phase="active",
        employee_id="00100", tools_enabled=True,
        metadata={"project_id": "oneonone-00100"},
        created_at="2026-03-18T10:00:00",
    )
    new_msg = Message(sender="ceo", role="CEO", text="how's the project?", timestamp="t1")

    mock_result = MagicMock()
    mock_result.output = "Project is on track."
    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=mock_result)

    with patch(
        "onemancompany.core.conversation_adapters._get_employee_executor",
        return_value=mock_executor,
    ):
        adapter = ClaudeSessionAdapter()
        reply = await adapter.send(conv, [], new_msg)

    assert reply == "Project is on track."
    mock_executor.execute.assert_awaited_once()
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation_adapters.py::test_claude_session_adapter_send -x -v`

- [ ] **Step 3: Implement ClaudeSessionAdapter**

Add to `src/onemancompany/core/conversation_adapters.py`:

```python
@register_adapter("claude_session")
class ClaudeSessionAdapter:
    async def send(
        self, conversation: Conversation, messages: list[Message], new_message: Message,
    ) -> str:
        executor = _get_employee_executor(conversation.employee_id)
        prompt = _build_conversation_prompt(conversation, messages, new_message)
        logger.debug(
            "[conversation] ClaudeSessionAdapter.send: employee={}, project_id={}",
            conversation.employee_id, conversation.metadata.get("project_id"),
        )
        from onemancompany.core.vessel import TaskContext
        ctx = TaskContext(
            employee_id=conversation.employee_id,
            project_id=conversation.metadata.get("project_id", ""),
        )
        result = await executor.execute(prompt, ctx)
        return result.output

    async def on_create(self, conversation: Conversation) -> None:
        pass

    async def on_close(self, conversation: Conversation) -> None:
        pass
```

Note: Both adapters share the same dispatch pattern (`executor.execute(prompt, ctx) -> LaunchResult.output`). The executor infrastructure handles the LangChain vs Claude session difference internally. `executor.execute()` requires a `TaskContext` second argument and returns a `LaunchResult` dataclass — use `.output` to get the text. If future needs diverge (e.g., streaming for Claude), this is where the split happens.

- [ ] **Step 4: Run test — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation_adapters.py -x -v`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/conversation_adapters.py tests/unit/core/test_conversation_adapters.py
git commit -m "feat(conversation): implement ClaudeSessionAdapter"
```

---

## Task 6: Close Hooks Registry + Implementations

**Files:**
- Create: `src/onemancompany/core/conversation_hooks.py`
- Test: `tests/unit/core/test_conversation_hooks.py`

**Context:** Close hooks run business logic when a conversation ends. Registry-based dispatch by conversation type. CEO inbox hook: generate summary + transition task node + resolve dependencies. 1-on-1 hook: generate reflection + update work_principles. Reference: `routes.py:5624-5662` (_run_conversation_loop completion), `routes.py:929-1054` (oneonone_end).

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/core/test_conversation_hooks.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from onemancompany.core.conversation import Conversation
from onemancompany.core.conversation_hooks import (
    register_close_hook, run_close_hook,
)


@pytest.mark.asyncio
async def test_run_close_hook_dispatches_by_type():
    results = {}

    @register_close_hook("test_type")
    async def _test_hook(conv):
        results["called"] = True
        results["conv_id"] = conv.id
        return {"status": "done"}

    conv = Conversation(
        id="c1", type="test_type", phase="closing",
        employee_id="00100", tools_enabled=False,
        created_at="2026-03-18T10:00:00",
    )
    result = await run_close_hook(conv, wait=True)
    assert results["called"] is True
    assert result == {"status": "done"}


@pytest.mark.asyncio
async def test_run_close_hook_unknown_type_returns_none():
    conv = Conversation(
        id="c2", type="unknown_type", phase="closing",
        employee_id="00100", tools_enabled=False,
        created_at="2026-03-18T10:00:00",
    )
    result = await run_close_hook(conv, wait=True)
    assert result is None
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation_hooks.py -x -v`

- [ ] **Step 3: Implement hooks registry**

```python
# src/onemancompany/core/conversation_hooks.py
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from loguru import logger

from onemancompany.core.conversation import Conversation

_close_hooks: dict[str, Callable[..., Awaitable[dict | None]]] = {}


def register_close_hook(conv_type: str):
    """Decorator to register a close hook for a conversation type."""
    def decorator(fn):
        _close_hooks[conv_type] = fn
        logger.debug("[conversation] registered close hook: {}", conv_type)
        return fn
    return decorator


async def run_close_hook(conv: Conversation, wait: bool = False) -> dict | None:
    """Run the close hook for a conversation type."""
    hook = _close_hooks.get(conv.type)
    if not hook:
        logger.debug("[conversation] no close hook for type={}", conv.type)
        return None

    logger.debug("[conversation] running close hook: type={}, wait={}", conv.type, wait)
    if wait:
        return await hook(conv)
    else:
        asyncio.create_task(hook(conv))
        return None
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation_hooks.py -x -v`

- [ ] **Step 5: Write failing tests for ceo_inbox and oneonone hooks**

```python
# tests/unit/core/test_conversation_hooks.py (append)

@pytest.mark.asyncio
async def test_ceo_inbox_close_hook():
    """Verify inbox hook generates summary and transitions node."""
    from onemancompany.core.conversation_hooks import _close_ceo_inbox

    conv = Conversation(
        id="c3", type="ceo_inbox", phase="closing",
        employee_id="00100", tools_enabled=False,
        metadata={"node_id": "node-1", "project_dir": "/tmp/test"},
        created_at="2026-03-18T10:00:00",
    )
    with patch("onemancompany.core.conversation_hooks._generate_summary", new_callable=AsyncMock, return_value="Summary text"), \
         patch("onemancompany.core.conversation_hooks._transition_inbox_node", new_callable=AsyncMock):
        result = await _close_ceo_inbox(conv)
    assert result is not None
    assert "summary" in result


@pytest.mark.asyncio
async def test_oneonone_close_hook():
    """Verify 1-on-1 hook generates reflection and updates principles."""
    from onemancompany.core.conversation_hooks import _close_oneonone

    conv = Conversation(
        id="c4", type="oneonone", phase="closing",
        employee_id="00100", tools_enabled=True,
        metadata={},
        created_at="2026-03-18T10:00:00",
    )
    with patch("onemancompany.core.conversation_hooks._generate_reflection", new_callable=AsyncMock, return_value={"reflection": "Good meeting", "principles_updated": True}), \
         patch("onemancompany.core.conversation_hooks._update_employee_principles", new_callable=AsyncMock):
        result = await _close_oneonone(conv)
    assert result["reflection"] == "Good meeting"
    assert result["principles_updated"] is True
```

- [ ] **Step 6: Run tests — expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation_hooks.py -x -v`

- [ ] **Step 7: Implement close hooks for ceo_inbox and oneonone**

Add to `src/onemancompany/core/conversation_hooks.py`:

```python
async def _generate_summary(conv: Conversation) -> str:
    """Generate conversation summary via LLM. Port from _run_conversation_loop."""
    from onemancompany.core.conversation import load_messages, _resolve_conv_dir
    messages = load_messages(_resolve_conv_dir(conv))
    if not messages:
        return ""
    # Reuse existing summary generation logic from ceo_conversation.py
    from onemancompany.core.ceo_conversation import _build_agent_and_invoke
    transcript = "\n".join(f"[{m.role}]: {m.text}" for m in messages)
    summary = await _build_agent_and_invoke(
        conv.employee_id,
        f"Summarize this conversation in 2-3 sentences:\n{transcript}",
        [], conv.metadata.get("node_id", ""), conv.metadata.get("project_dir", ""),
    )
    return summary


async def _transition_inbox_node(conv: Conversation) -> None:
    """Transition ceo_request node through completed -> accepted.

    Port from routes.py _run_conversation_loop (lines 5635-5657).
    Must handle: node transition, tree save, dep resolution, parent auto-resume.
    """
    from onemancompany.core.task_lifecycle import transition
    from onemancompany.core.task_tree import load_tree, save_tree_async

    node_id = conv.metadata.get("node_id")
    project_dir = conv.metadata.get("project_dir")
    if not node_id or not project_dir:
        return

    tree = load_tree(project_dir)
    node = tree.find_node(node_id)
    if not node:
        logger.warning("[conversation] inbox node not found: {}", node_id)
        return

    # Set result from summary
    # Transition: processing -> completed -> accepted (simple task auto-skip)
    transition(node, "completed")
    await save_tree_async(project_dir, tree)

    # Trigger dependency resolution for downstream nodes
    from onemancompany.api.routes import _trigger_dep_resolution
    await _trigger_dep_resolution(tree, project_dir)

    # Auto-resume parent if it was HOLDING for this ceo_request
    parent = tree.find_parent(node_id)
    if parent and parent.phase == "holding":
        transition(parent, "processing")
        await save_tree_async(project_dir, tree)

    logger.debug("[conversation] transitioned inbox node: {}", node_id)


async def _generate_reflection(conv: Conversation) -> dict:
    """Generate reflection notes for 1-on-1.

    Port from routes.py oneonone_end (lines 929-1054).
    Must handle: UPDATED/NO_UPDATE detection, SUMMARY extraction,
    work_principles save, guidance note save, is_listening reset, guidance_end event.
    """
    from onemancompany.core.conversation import load_messages, _resolve_conv_dir
    from onemancompany.core.ceo_conversation import _build_agent_and_invoke

    messages = load_messages(_resolve_conv_dir(conv))
    transcript = "\n".join(f"[{m.role}]: {m.text}" for m in messages)

    # Use the same prompt format as routes.py oneonone_end
    # Ask LLM to reflect and output UPDATED/NO_UPDATE + SUMMARY sections
    reflection = await _build_agent_and_invoke(
        conv.employee_id,
        f"Reflect on this 1-on-1 meeting and extract key takeaways:\n{transcript}\n\n"
        "If the CEO gave specific guidance, output UPDATED: followed by the updated work principles.\n"
        "If no changes needed, output NO_UPDATE.\n"
        "Then output SUMMARY: followed by a brief meeting summary.",
        [], "", "",
    )

    principles_updated = "UPDATED:" in reflection
    return {"reflection": reflection, "principles_updated": principles_updated}


async def _update_employee_principles(conv: Conversation, reflection: dict) -> None:
    """Update work_principles + guidance. Port from oneonone_end."""
    from onemancompany.core import store as _store
    from onemancompany.core.events import event_bus, CompanyEvent

    # Parse UPDATED section for principles
    text = reflection.get("reflection", "")
    if reflection.get("principles_updated") and "UPDATED:" in text:
        updated_text = text.split("UPDATED:")[1].split("SUMMARY:")[0].strip()
        await _store.save_work_principles(conv.employee_id, updated_text)

    # Parse SUMMARY section for guidance note
    if "SUMMARY:" in text:
        summary = text.split("SUMMARY:")[1].strip()
        await _store.save_guidance(conv.employee_id, [summary])

    # Reset is_listening flag
    await _store.save_employee_runtime(conv.employee_id, is_listening=False)

    # Publish guidance_end event
    await event_bus.publish(CompanyEvent(
        type="guidance_end",
        payload={"employee_id": conv.employee_id},
        agent="system",
    ))
    logger.debug("[conversation] updated principles for {}", conv.employee_id)


@register_close_hook("ceo_inbox")
async def _close_ceo_inbox(conv: Conversation) -> dict | None:
    summary = await _generate_summary(conv)
    await _transition_inbox_node(conv)
    logger.debug("[conversation] ceo_inbox close hook done: {}", conv.id)
    return {"summary": summary}


@register_close_hook("oneonone")
async def _close_oneonone(conv: Conversation) -> dict | None:
    reflection = await _generate_reflection(conv)
    await _update_employee_principles(conv, reflection)
    logger.debug("[conversation] oneonone close hook done: {}", conv.id)
    return reflection
```

- [ ] **Step 8: Run tests — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation_hooks.py -x -v`

- [ ] **Step 9: Commit**

```bash
git add src/onemancompany/core/conversation_hooks.py tests/unit/core/test_conversation_hooks.py
git commit -m "feat(conversation): add close hooks with ceo_inbox and oneonone implementations"
```

---

## Task 7: Event Types + WebSocket Integration

**Files:**
- Modify: `src/onemancompany/core/events.py`
- Test: `tests/unit/core/test_conversation.py`

**Context:** Add `conversation_message` and `conversation_phase` to EventType literal. Wire ConversationService to publish events via event_bus after message persistence and phase changes.

- [ ] **Step 1: Add event types to EventType literal**

Read `src/onemancompany/core/events.py`, find the `EventType` Literal definition, add `"conversation_message"` and `"conversation_phase"`.

- [ ] **Step 2: Write failing test for event publishing**

```python
# tests/unit/core/test_conversation.py (append)

@pytest.mark.asyncio
async def test_send_message_publishes_event(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    conv = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)

    published_events = []
    async def mock_publish(event):
        published_events.append(event)

    monkeypatch.setattr("onemancompany.core.conversation.event_bus.publish", mock_publish)

    msg = await svc.send_message(conv.id, sender="ceo", role="CEO", text="hi")
    assert len(published_events) == 1
    assert published_events[0].type == "conversation_message"
    assert published_events[0].payload["conv_id"] == conv.id
    assert published_events[0].payload["text"] == "hi"
```

- [ ] **Step 3: Run test — expect FAIL**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation.py::test_send_message_publishes_event -x -v`

- [ ] **Step 4: Wire event publishing into ConversationService**

Update `send_message` in `conversation.py` to publish `conversation_message` event after persisting:

```python
# At top of conversation.py, add:
from onemancompany.core.events import event_bus, CompanyEvent

# In send_message, after append_message:
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
```

Similarly wire `conversation_phase` events in `create` and `close`.

- [ ] **Step 5: Run test — expect PASS**

Run: `.venv/bin/python -m pytest tests/unit/core/test_conversation.py -x -v`

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/events.py src/onemancompany/core/conversation.py tests/unit/core/test_conversation.py
git commit -m "feat(conversation): add event types and wire event publishing"
```

---

## Task 8: Unified REST API Endpoints

**Files:**
- Modify: `src/onemancompany/api/routes.py`
- Test: `tests/integration/test_conversation_api.py`

**Context:** Add the unified `/api/conversation/*` endpoints that delegate to ConversationService. These run alongside existing legacy endpoints. The `send_message` endpoint returns immediately; agent reply comes via WebSocket. The `close` endpoint supports `?wait_hooks=true` for blocking close (1-on-1).

- [ ] **Step 1: Write integration tests for API endpoints**

```python
# tests/integration/test_conversation_api.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from onemancompany.api.routes import app  # or however the FastAPI app is exported


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_conversation(client, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone",
        "employee_id": "00100",
        "tools_enabled": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    assert data["phase"] == "active"


@pytest.mark.asyncio
async def test_send_message(client, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    # Create conversation first
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
    })
    conv_id = resp.json()["id"]

    # Send message
    resp = await client.post(f"/api/conversation/{conv_id}/message", json={
        "text": "hello",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


@pytest.mark.asyncio
async def test_get_messages(client, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
    })
    conv_id = resp.json()["id"]

    await client.post(f"/api/conversation/{conv_id}/message", json={"text": "hi"})

    resp = await client.get(f"/api/conversation/{conv_id}/messages")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert len(msgs) >= 1
    assert msgs[0]["text"] == "hi"


@pytest.mark.asyncio
async def test_close_conversation(client, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    resp = await client.post("/api/conversation/create", json={
        "type": "oneonone", "employee_id": "00100", "tools_enabled": True,
    })
    conv_id = resp.json()["id"]

    with patch("onemancompany.core.conversation_hooks.run_close_hook", new_callable=AsyncMock, return_value=None):
        resp = await client.post(f"/api/conversation/{conv_id}/close")
    assert resp.status_code == 200
    assert resp.json()["phase"] == "closed"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/bin/python -m pytest tests/integration/test_conversation_api.py -x -v`

- [ ] **Step 3: Implement API endpoints in routes.py**

Add to `routes.py`:

```python
# Unified Conversation API
from onemancompany.core.conversation import ConversationService

_conversation_service = ConversationService()

@router.post("/api/conversation/create")
async def create_conversation(body: dict):
    conv = await _conversation_service.create(
        type=body["type"],
        employee_id=body["employee_id"],
        tools_enabled=body.get("tools_enabled", False),
        **{k: v for k, v in body.items() if k not in ("type", "employee_id", "tools_enabled")},
    )
    return conv.to_dict()

@router.get("/api/conversation/{conv_id}")
async def get_conversation(conv_id: str):
    conv = _conversation_service.get(conv_id)
    return conv.to_dict()

@router.get("/api/conversation/{conv_id}/messages")
async def get_conversation_messages(conv_id: str):
    msgs = _conversation_service.get_messages(conv_id)
    return {"messages": [m.to_dict() for m in msgs]}

@router.post("/api/conversation/{conv_id}/message")
async def send_conversation_message(conv_id: str, body: dict):
    # Persist CEO message
    msg = await _conversation_service.send_message(
        conv_id, sender="ceo", role="CEO", text=body["text"],
        attachments=body.get("attachments"),
    )
    # Dispatch to adapter in background
    asyncio.create_task(_dispatch_to_adapter(conv_id, msg))
    return {"status": "sent", "message": msg.to_dict()}

@router.post("/api/conversation/{conv_id}/upload")
async def upload_conversation_files(conv_id: str, files: list[UploadFile]):
    conv = _conversation_service.get(conv_id)
    saved_paths = []
    for file in files:
        # Save to workspace
        workspace = Path(conv.metadata.get("project_dir", ".")) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        dest = workspace / file.filename
        content = await file.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))
    return {"attachments": saved_paths}

@router.post("/api/conversation/{conv_id}/close")
async def close_conversation(conv_id: str, wait_hooks: bool = False):
    result = await _conversation_service.close(conv_id, wait_hooks=wait_hooks)
    conv = _conversation_service.get(conv_id)
    resp = conv.to_dict()
    if result:
        resp["hook_result"] = result
    return resp

@router.get("/api/conversations")
async def list_conversations(type: str | None = None, phase: str | None = None):
    if phase and phase != "active":
        convs = _conversation_service.list_by_phase(type=type, phase=phase)
    else:
        convs = _conversation_service.list_active(type=type)
    return {"conversations": [c.to_dict() for c in convs]}


async def _dispatch_to_adapter(conv_id: str, ceo_message: Message):
    """Background task: dispatch CEO message to adapter, persist reply."""
    try:
        conv = _conversation_service.get(conv_id)
        messages = _conversation_service.get_messages(conv_id)

        from onemancompany.core.conversation_adapters import get_adapter, _get_executor_type
        from onemancompany.core import store as _store

        executor_type = _get_executor_type(conv.employee_id)
        adapter_cls = get_adapter(executor_type)
        adapter = adapter_cls()
        reply_text = await adapter.send(conv, messages[:-1], ceo_message)

        # Persist agent reply — get employee name from disk (SSOT)
        emp_data = _store.load_employee(conv.employee_id)
        emp_name = emp_data.get("name", conv.employee_id) if emp_data else conv.employee_id
        await _conversation_service.send_message(
            conv_id, sender=conv.employee_id, role=emp_name, text=reply_text,
        )
    except Exception:
        logger.exception("[conversation] adapter dispatch failed for {}", conv_id)
        # Send error message so CEO sees feedback
        await _conversation_service.send_message(
            conv_id, sender="system", role="System",
            text="Agent is not responding. Please try again.",
        )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/bin/python -m pytest tests/integration/test_conversation_api.py -x -v`

- [ ] **Step 5: Wire ConversationService startup into main.py**

In `src/onemancompany/main.py`, add to the startup sequence:

```python
# After existing startup code (snapshot restore, employee registration, etc.)
from onemancompany.api.routes import _conversation_service
_conversation_service.rebuild_index()
logger.info("[startup] ConversationService index rebuilt: {} conversations", len(_conversation_service._index))
```

This ensures the conversation index is populated from disk after restart.

- [ ] **Step 6: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`

- [ ] **Step 7: Commit**

```bash
git add src/onemancompany/api/routes.py src/onemancompany/main.py tests/integration/test_conversation_api.py
git commit -m "feat(conversation): add unified REST API endpoints + startup wiring"
```

---

## Task 9: Frontend ChatPanel Component

**Files:**
- Create: `frontend/conversation.js`
- Modify: `frontend/index.html`
- Modify: `frontend/style.css`

**Context:** A reusable ChatPanel class that renders messages, handles send/close actions, shows typing indicator. Pixel-art style matching existing UI. Referenced by RightPanel in Task 10.

- [ ] **Step 1: Create ChatPanel class**

```javascript
// frontend/conversation.js

class ChatPanel {
    constructor(containerEl) {
        this._container = containerEl;
        this._messagesEl = null;
        this._inputEl = null;
        this._sendBtn = null;
        this._closeBtn = null;
        this._typingEl = null;
        this._onSendCb = null;
        this._onCloseCb = null;
        this._convId = null;
        this._convType = null;
        this._render();
    }

    _render() {
        this._container.innerHTML = `
            <div class="chat-panel">
                <div class="chat-panel-header">
                    <span class="chat-panel-type"></span>
                    <span class="chat-panel-employee"></span>
                    <button class="chat-panel-close-btn">End</button>
                </div>
                <div class="chat-panel-messages"></div>
                <div class="chat-panel-typing hidden">typing...</div>
                <div class="chat-panel-input-row">
                    <textarea class="chat-panel-input" rows="2" placeholder="Type a message..."></textarea>
                    <div class="chat-panel-actions">
                        <label class="chat-panel-upload-label">
                            <input type="file" class="chat-panel-file" multiple hidden />
                            +
                        </label>
                        <button class="chat-panel-send-btn">Send</button>
                    </div>
                </div>
            </div>
        `;
        this._messagesEl = this._container.querySelector('.chat-panel-messages');
        this._inputEl = this._container.querySelector('.chat-panel-input');
        this._sendBtn = this._container.querySelector('.chat-panel-send-btn');
        this._closeBtn = this._container.querySelector('.chat-panel-close-btn');
        this._typingEl = this._container.querySelector('.chat-panel-typing');
        this._fileInput = this._container.querySelector('.chat-panel-file');

        this._sendBtn.addEventListener('click', () => this._handleSend());
        this._closeBtn.addEventListener('click', () => {
            if (this._onCloseCb) this._onCloseCb(this._convId);
        });
        this._inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this._handleSend();
            }
        });
    }

    async _handleSend() {
        const text = this._inputEl.value.trim();
        if (!text || !this._onSendCb) return;
        this._inputEl.value = '';

        // Upload files first if selected
        let attachments = [];
        if (this._fileInput.files.length > 0) {
            const formData = new FormData();
            for (const file of this._fileInput.files) {
                formData.append('files', file);
            }
            try {
                const resp = await fetch(`/api/conversation/${this._convId}/upload`, {
                    method: 'POST', body: formData,
                });
                const data = await resp.json();
                attachments = data.attachments || [];
            } catch (err) {
                console.error('Upload failed:', err);
            }
            this._fileInput.value = '';
        }

        this._onSendCb(this._convId, text, attachments);
    }

    setConversation(convId, convType, employeeName) {
        this._convId = convId;
        this._convType = convType;
        this._container.querySelector('.chat-panel-type').textContent =
            convType === 'oneonone' ? '1-on-1' : 'Inbox';
        this._container.querySelector('.chat-panel-employee').textContent = employeeName;
    }

    renderMessages(messages) {
        this._messagesEl.innerHTML = '';
        for (const msg of messages) {
            this._appendMessageEl(msg);
        }
        this._messagesEl.scrollTop = this._messagesEl.scrollHeight;
    }

    appendMessage(msg) {
        this._appendMessageEl(msg);
        this._messagesEl.scrollTop = this._messagesEl.scrollHeight;
        this.showTyping(false);
    }

    _appendMessageEl(msg) {
        const div = document.createElement('div');
        const isCeo = msg.sender === 'ceo';
        div.className = `chat-msg ${isCeo ? 'chat-msg-ceo' : 'chat-msg-agent'}`;
        div.innerHTML = `
            <div class="chat-msg-role">${this._escapeHtml(msg.role)}</div>
            <div class="chat-msg-text">${this._escapeHtml(msg.text)}</div>
            ${msg.attachments && msg.attachments.length
                ? `<div class="chat-msg-attachments">${msg.attachments.map(a =>
                    `<span class="chat-msg-attachment">${this._escapeHtml(a.split('/').pop())}</span>`
                  ).join('')}</div>`
                : ''}
        `;
        this._messagesEl.appendChild(div);
    }

    setInputEnabled(enabled) {
        this._inputEl.disabled = !enabled;
        this._sendBtn.disabled = !enabled;
        this._closeBtn.style.display = enabled ? '' : 'none';
    }

    showTyping(show) {
        this._typingEl.classList.toggle('hidden', !show);
    }

    onSend(cb) { this._onSendCb = cb; }
    onClose(cb) { this._onCloseCb = cb; }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}
```

- [ ] **Step 2: Add ChatPanel styles to style.css**

```css
/* ChatPanel styles */
.chat-panel { display: flex; flex-direction: column; height: 100%; }
.chat-panel-header {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 8px; border-bottom: 1px solid var(--border-color, #333);
    font-size: 7px; font-family: monospace;
}
.chat-panel-type {
    background: var(--accent-color, #4a6); color: #000;
    padding: 1px 4px; font-weight: bold;
}
.chat-panel-employee { flex: 1; }
.chat-panel-close-btn {
    font-size: 7px; font-family: monospace; cursor: pointer;
    background: #633; color: #fff; border: 1px solid #966; padding: 1px 6px;
}
.chat-panel-messages {
    flex: 1; overflow-y: auto; padding: 4px;
    display: flex; flex-direction: column; gap: 4px;
}
.chat-msg { max-width: 80%; padding: 4px 6px; font-size: 7px; font-family: monospace; }
.chat-msg-ceo { align-self: flex-start; background: var(--ceo-msg-bg, #234); border: 1px solid #456; }
.chat-msg-agent { align-self: flex-end; background: var(--agent-msg-bg, #342); border: 1px solid #564; }
.chat-msg-role { font-weight: bold; margin-bottom: 2px; font-size: 6px; opacity: 0.7; }
.chat-msg-text { white-space: pre-wrap; word-break: break-word; }
.chat-msg-attachments { margin-top: 2px; font-size: 6px; opacity: 0.6; }
.chat-msg-attachment { margin-right: 4px; }
.chat-panel-typing { padding: 2px 8px; font-size: 6px; opacity: 0.5; font-style: italic; }
.chat-panel-input-row {
    display: flex; gap: 4px; padding: 4px;
    border-top: 1px solid var(--border-color, #333);
}
.chat-panel-input {
    flex: 1; font-size: 7px; font-family: monospace; resize: none;
    background: var(--input-bg, #111); color: var(--text-color, #ddd);
    border: 1px solid var(--border-color, #333); padding: 4px;
}
.chat-panel-actions { display: flex; flex-direction: column; gap: 2px; }
.chat-panel-send-btn {
    font-size: 7px; font-family: monospace; cursor: pointer;
    background: var(--accent-color, #4a6); color: #000; border: none; padding: 2px 8px;
}
.chat-panel-upload-label { cursor: pointer; font-size: 7px; text-align: center; }
```

- [ ] **Step 3: Add script tag to index.html**

Add `<script src="conversation.js"></script>` before `app.js` in `index.html`.

- [ ] **Step 4: Manual verification**

Open browser, check ChatPanel renders correctly. Verify pixel-art styling matches existing UI.

- [ ] **Step 5: Commit**

```bash
git add frontend/conversation.js frontend/style.css frontend/index.html
git commit -m "feat(conversation): add ChatPanel frontend component"
```

---

## Task 10: Right Panel Integration + WebSocket Wiring

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

**Context:** Wire ChatPanel into the right panel of the existing 3-column layout. Add WebSocket handler for `conversation_message` and `conversation_phase` events. Entry points: clicking employee in office shows "Start 1-on-1" button, clicking ceo_request in task tree opens inbox conversation.

- [ ] **Step 1: Add right panel conversation container to index.html**

Add a `<div id="right-panel-chat" class="hidden"></div>` inside the right panel area of `index.html`.

- [ ] **Step 2: Add WebSocket handler for conversation events in app.js**

In the `handleMessage(msg)` switch/dispatch:

```javascript
case 'conversation_message':
    if (this._chatPanel && msg.conv_id === this._chatPanel._convId) {
        this._chatPanel.appendMessage(msg);
    }
    break;
case 'conversation_phase':
    if (msg.phase === 'closed' && this._chatPanel && msg.conv_id === this._chatPanel._convId) {
        this._chatPanel.setInputEnabled(false);
    }
    break;
```

- [ ] **Step 3: Add conversation open/send/close methods to AppController**

```javascript
async _openConversation(convId) {
    const chatContainer = document.getElementById('right-panel-chat');
    chatContainer.classList.remove('hidden');
    // Hide employee info panel
    document.getElementById('employee-detail')?.classList.add('hidden');

    if (!this._chatPanel) {
        this._chatPanel = new ChatPanel(chatContainer);
        this._chatPanel.onSend((id, text, attachments) => this._sendConversationMessage(id, text, attachments));
        this._chatPanel.onClose((id) => this._closeConversation(id));
    }

    // Fetch conversation + messages
    const [convResp, msgsResp] = await Promise.all([
        fetch(`/api/conversation/${convId}`).then(r => r.json()),
        fetch(`/api/conversation/${convId}/messages`).then(r => r.json()),
    ]);

    const empName = this._getEmployeeName(convResp.employee_id) || convResp.employee_id;
    this._chatPanel.setConversation(convId, convResp.type, empName);
    this._chatPanel.renderMessages(msgsResp.messages);
    this._chatPanel.setInputEnabled(convResp.phase === 'active');
}

async _startOneononeConversation(employeeId) {
    const resp = await fetch('/api/conversation/create', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            type: 'oneonone', employee_id: employeeId, tools_enabled: true,
        }),
    });
    const conv = await resp.json();
    await this._openConversation(conv.id);
}

async _sendConversationMessage(convId, text, attachments) {
    this._chatPanel.showTyping(true);
    await fetch(`/api/conversation/${convId}/message`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text, attachments }),
    });
    // Reply arrives via WebSocket conversation_message event
}

async _closeConversation(convId) {
    const conv = this._chatPanel._convType;
    const waitHooks = conv === 'oneonone';
    const resp = await fetch(`/api/conversation/${convId}/close?wait_hooks=${waitHooks}`, {
        method: 'POST',
    });
    this._chatPanel.setInputEnabled(false);
    // Return to employee info
    const chatContainer = document.getElementById('right-panel-chat');
    chatContainer.classList.add('hidden');
    document.getElementById('employee-detail')?.classList.remove('hidden');
}
```

- [ ] **Step 4: Add "Start 1-on-1" button to employee info panel**

In the employee info render function, add a button:

```javascript
// In _showEmployeeDetail or equivalent
const btn = document.createElement('button');
btn.textContent = 'Start 1-on-1';
btn.className = 'start-oneonone-btn';
btn.onclick = () => this._startOneononeConversation(employeeId);
```

- [ ] **Step 5: Wire ceo_request task tree click to open conversation**

In the task tree click handler, when node type is `ceo_request`:

```javascript
// If node has conversation_id in metadata, open it directly
if (node.metadata?.conversation_id) {
    this._openConversation(node.metadata.conversation_id);
} else {
    // Legacy: create new conversation from inbox
    const resp = await fetch('/api/conversation/create', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            type: 'ceo_inbox', employee_id: node.employee_id,
            tools_enabled: false, node_id: node.id,
            project_dir: node.project_dir,
        }),
    });
    const conv = await resp.json();
    await this._openConversation(conv.id);
}
```

- [ ] **Step 6: Manual verification**

Test full flow:
1. Click employee in office → see "Start 1-on-1" button → click → ChatPanel opens
2. Send message → see typing indicator → agent reply arrives
3. End conversation → panel closes, returns to employee info
4. Click ceo_request in task tree → inbox conversation opens
5. Verify WebSocket events drive real-time updates

- [ ] **Step 7: Verify no JS syntax errors**

Run: `node -c frontend/conversation.js && node -c frontend/app.js`

- [ ] **Step 8: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(conversation): wire ChatPanel into right panel with WebSocket"
```

---

## Task 11: Legacy API Rewiring

**Files:**
- Modify: `src/onemancompany/api/routes.py`
- Test: `tests/integration/test_conversation_api.py`

**Context:** Rewire existing `/api/ceo/inbox/*` and `/api/oneonone/*` endpoints to internally call ConversationService. This maintains backward compatibility during the transition period. Legacy endpoints remain functional but delegate to the new service.

- [ ] **Step 1: Write tests for legacy endpoint compatibility**

```python
# tests/integration/test_conversation_api.py (append)

@pytest.mark.asyncio
async def test_legacy_ceo_inbox_open_creates_conversation(client, tmp_path, monkeypatch):
    """Legacy /api/ceo/inbox/{node_id}/open should create a unified conversation."""
    from unittest.mock import MagicMock

    mock_node = MagicMock()
    mock_node.id = "node-test"
    mock_node.metadata = {}
    mock_tree = MagicMock()
    project_dir = str(tmp_path / "projects" / "p1")

    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    with patch(
        "onemancompany.api.routes._find_ceo_node",
        return_value=(mock_node, mock_tree, project_dir),
    ):
        resp = await client.post("/api/ceo/inbox/node-test/open")
    assert resp.status_code == 200
    data = resp.json()
    # Verify a conversation_id was assigned
    assert "conversation_id" in data or "messages" in data


@pytest.mark.asyncio
async def test_legacy_oneonone_chat_uses_conversation_service(client, tmp_path, monkeypatch):
    """Legacy /api/oneonone/chat should route through ConversationService."""
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    mock_result = MagicMock()
    mock_result.output = "Agent response"
    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=mock_result)

    with patch(
        "onemancompany.api.routes.employee_manager.executors",
        {"00100": mock_executor},
    ):
        resp = await client.post("/api/oneonone/chat", json={
            "employee_id": "00100",
            "message": "hello",
            "history": [],
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "reply" in data or "response" in data
```

- [ ] **Step 2: Rewire legacy CEO inbox endpoints**

In `routes.py`, modify `open_ceo_conversation` to:
1. Create a `Conversation` via `_conversation_service.create(type="ceo_inbox", ...)`
2. Store `conversation_id` on the task tree node metadata
3. Use `_conversation_service.send_message()` for message persistence
4. Use `_conversation_service.close()` for completion

Keep the same endpoint paths and response shapes for frontend compatibility.

- [ ] **Step 3: Rewire legacy 1-on-1 endpoints**

In `routes.py`, modify `oneonone_chat` to:
1. On first message: create `Conversation` via `_conversation_service.create(type="oneonone", ...)`
2. Store conversation_id in a session-local mapping (employee_id -> conv_id)
3. Route messages through `_conversation_service.send_message()`
4. `oneonone_end` calls `_conversation_service.close(wait_hooks=True)`

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -x -v`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/api/routes.py tests/integration/test_conversation_api.py
git commit -m "feat(conversation): rewire legacy endpoints to use ConversationService"
```

---

## Task 12: Regression Testing + Cleanup

**Files:**
- Test: `tests/integration/test_conversation_api.py`
- All modified files

**Context:** Final pass. Run the full regression checklist from the spec. Verify no existing functionality is broken.

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -v`

Fix any failures.

- [ ] **Step 2: Manual regression checklist**

Start the server and test:

1. [ ] CEO inbox: create request → open conversation → multi-turn → close → summary generated
2. [ ] 1-on-1: click employee → start → chat → end → reflection + principles updated
3. [ ] Concurrent: have inbox + 1-on-1 active, switch between them
4. [ ] Right panel: click different employees, verify panel switches correctly
5. [ ] WebSocket: verify both legacy and new events fire during transition
6. [ ] Server restart: stop/start server, verify active conversations recover

- [ ] **Step 3: Verify compilation and imports**

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`
Run: `.venv/bin/python -c "from onemancompany.core.conversation import ConversationService; print('OK')"`
Run: `.venv/bin/python -c "from onemancompany.core.conversation_adapters import get_adapter; print('OK')"`
Run: `.venv/bin/python -c "from onemancompany.core.conversation_hooks import run_close_hook; print('OK')"`
Run: `node -c frontend/conversation.js && node -c frontend/app.js`

- [ ] **Step 4: Final commit**

```bash
git add tests/integration/test_conversation_api.py
git commit -m "test(conversation): add regression tests and verify full integration"
```

---

## Task Summary

| Task | Description | Dependencies |
|------|-------------|-------------|
| 1 | Data models + disk persistence | None |
| 2 | ConversationService core | Task 1 |
| 3 | Adapter protocol + registry | Task 1 |
| 4 | LangChainAdapter | Task 3 |
| 5 | ClaudeSessionAdapter | Task 3 |
| 6 | Close hooks | Task 2 |
| 7 | Event types + WebSocket | Task 2 |
| 8 | REST API endpoints | Tasks 2, 3, 7 |
| 9 | Frontend ChatPanel | None (independent) |
| 10 | Right panel integration | Tasks 8, 9 |
| 11 | Legacy API rewiring | Tasks 2, 8 |
| 12 | Regression testing | All |

**Parallelizable:** Tasks 4+5 (both adapters), Tasks 3+6 (adapter registry + hooks), Task 9 (frontend, independent of backend).
