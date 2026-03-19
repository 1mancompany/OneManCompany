"""Conversation adapter protocol and registry.

Each executor type (LangChain, Claude session, etc.) provides an adapter
that knows how to send messages and manage lifecycle for conversations.
Adapters are registered via the ``@register_adapter`` decorator and looked
up by executor type string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from loguru import logger

from onemancompany.core.conversation import Conversation, Message
from onemancompany.core.models import ConversationType

# Single-file constants
EXECUTOR_TYPE_LANGCHAIN = "langchain"
EXECUTOR_TYPE_CLAUDE_SESSION = "claude_session"


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


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _get_employee_executor(employee_id: str):
    """Get the Launcher for an employee. Lazy import to avoid circular deps."""
    from onemancompany.core.vessel import employee_manager

    executor = employee_manager.executors.get(employee_id)
    if not executor:
        raise ValueError(f"No executor for employee {employee_id}")
    return executor


_EXECUTOR_CLASS_MAP: dict[str, str] = {
    "ClaudeSessionExecutor": EXECUTOR_TYPE_CLAUDE_SESSION,
    "LangChainExecutor": EXECUTOR_TYPE_LANGCHAIN,
    "EmployeeAgent": EXECUTOR_TYPE_LANGCHAIN,
}


def _get_executor_type(employee_id: str) -> str:
    """Determine executor type string from Launcher subclass."""
    executor = _get_employee_executor(employee_id)
    cls_name = type(executor).__name__
    executor_type = _EXECUTOR_CLASS_MAP.get(cls_name)
    if executor_type is None:
        logger.warning(
            "[conversation] unknown executor class '{}' for employee {}, defaulting to langchain",
            cls_name, employee_id,
        )
        executor_type = EXECUTOR_TYPE_LANGCHAIN
    return executor_type


def _build_conversation_prompt(
    conversation: Conversation, messages: list[Message], new_message: Message,
) -> str:
    """Build a prompt with conversation history for the executor."""
    from onemancompany.core.config import get_workspace_dir

    lines = []
    lines.append("You are in a conversation with the CEO.")
    if conversation.type == ConversationType.ONE_ON_ONE:
        lines.append("This is a 1-on-1 meeting. Be direct and professional.")
        workspace_dir = get_workspace_dir(conversation.employee_id).resolve()
        lines.append(
            f"Use this workspace for all files/artifacts in this meeting: {workspace_dir}"
        )
        lines.append(
            "Never create files in repository-root ./workspace; always use your employee workspace path above."
        )
        shared_prompt = _load_oneonone_workspace_shared_prompt()
        if shared_prompt:
            lines.append("\n--- Workspace Policy (Shared Prompt) ---")
            lines.append(shared_prompt)
    elif conversation.type == ConversationType.CEO_INBOX:
        lines.append("The CEO is responding to your request. Answer their questions.")

    if messages:
        lines.append("\n--- Conversation History ---")
        for msg in messages:
            lines.append(f"[{msg.role}]: {msg.text}")

    lines.append(f"\n[{new_message.role}]: {new_message.text}")
    lines.append("\nPlease respond:")
    return "\n".join(lines)


def _load_oneonone_workspace_shared_prompt() -> str:
    """Load workspace policy prompt for one-on-one from shared_prompts."""
    from onemancompany.core.config import SHARED_PROMPTS_DIR, SOURCE_ROOT

    candidates = [
        SHARED_PROMPTS_DIR / "oneonone_workspace_policy.md",
        SOURCE_ROOT / "company" / "shared_prompts" / "oneonone_workspace_policy.md",
    ]
    for path in candidates:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("[conversation] failed to read workspace policy prompt: {}", path)
    return ""


def _resolve_conversation_work_dir(conversation: Conversation) -> str:
    """Resolve work_dir for an interactive conversation."""
    from onemancompany.core.config import get_workspace_dir

    # 1-on-1 should always use employee private workspace.
    if conversation.type == ConversationType.ONE_ON_ONE:
        ws = get_workspace_dir(conversation.employee_id).resolve()
        ws.mkdir(parents=True, exist_ok=True)
        return str(ws)

    # CEO inbox can inherit project_dir if provided, else fallback to employee workspace.
    project_dir = (conversation.metadata or {}).get("project_dir", "")
    if project_dir:
        p = Path(project_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    ws = get_workspace_dir(conversation.employee_id).resolve()
    ws.mkdir(parents=True, exist_ok=True)
    return str(ws)


# ---------------------------------------------------------------------------
# Concrete adapters
# ---------------------------------------------------------------------------


class _BaseConversationAdapter:
    """Shared send logic — both executor types use the same prompt + execute flow."""

    async def send(
        self, conversation: Conversation, messages: list[Message], new_message: Message,
    ) -> str:
        from onemancompany.core.runtime_context import _interaction_type, _interaction_work_dir
        executor = _get_employee_executor(conversation.employee_id)
        prompt = _build_conversation_prompt(conversation, messages, new_message)
        work_dir = _resolve_conversation_work_dir(conversation)
        logger.debug(
            "[conversation] {}.send: employee={}, project_id={}, work_dir={}",
            type(self).__name__, conversation.employee_id,
            conversation.metadata.get("project_id"),
            work_dir,
        )
        from onemancompany.core.vessel import TaskContext

        ctx = TaskContext(
            employee_id=conversation.employee_id,
            project_id=conversation.metadata.get("project_id", ""),
            work_dir=work_dir,
        )
        tok_type = _interaction_type.set(conversation.type)
        tok_work = _interaction_work_dir.set(work_dir)
        try:
            result = await executor.execute(prompt, ctx)
            return result.output
        finally:
            _interaction_type.reset(tok_type)
            _interaction_work_dir.reset(tok_work)

    async def on_create(self, conversation: Conversation) -> None:
        pass

    async def on_close(self, conversation: Conversation) -> None:
        pass


@register_adapter(EXECUTOR_TYPE_LANGCHAIN)
class LangChainAdapter(_BaseConversationAdapter):
    pass


@register_adapter(EXECUTOR_TYPE_CLAUDE_SESSION)
class ClaudeSessionAdapter(_BaseConversationAdapter):
    pass
