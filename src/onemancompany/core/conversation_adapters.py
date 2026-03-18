"""Conversation adapter protocol and registry.

Each executor type (LangChain, Claude session, etc.) provides an adapter
that knows how to send messages and manage lifecycle for conversations.
Adapters are registered via the ``@register_adapter`` decorator and looked
up by executor type string.
"""

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


# ---------------------------------------------------------------------------
# Concrete adapters
# ---------------------------------------------------------------------------


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
