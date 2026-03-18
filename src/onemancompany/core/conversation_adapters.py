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
