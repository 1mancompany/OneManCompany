import pytest
from unittest.mock import MagicMock
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
