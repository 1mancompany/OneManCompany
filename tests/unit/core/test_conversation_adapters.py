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


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock, patch


def test_get_employee_executor_missing():
    from onemancompany.core.conversation_adapters import _get_employee_executor

    mock_mgr = MagicMock()
    mock_mgr.executors = {}
    with patch("onemancompany.core.vessel.employee_manager", mock_mgr):
        with pytest.raises(ValueError, match="No executor"):
            _get_employee_executor("99999")


def test_get_executor_type_langchain():
    from onemancompany.core.conversation_adapters import _get_executor_type

    class FakeLangChainExecutor:
        pass

    mock_mgr = MagicMock()
    mock_mgr.executors = {"00100": FakeLangChainExecutor()}
    with patch("onemancompany.core.vessel.employee_manager", mock_mgr):
        assert _get_executor_type("00100") == "langchain"


def test_get_executor_type_claude_session():
    from onemancompany.core.conversation_adapters import _get_executor_type

    class ClaudeSessionExecutor:
        pass

    mock_mgr = MagicMock()
    mock_mgr.executors = {"00100": ClaudeSessionExecutor()}
    with patch("onemancompany.core.vessel.employee_manager", mock_mgr):
        assert _get_executor_type("00100") == "claude_session"


def test_build_conversation_prompt_with_history():
    from onemancompany.core.conversation_adapters import _build_conversation_prompt

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

    prompt = _build_conversation_prompt(conv, history, new_msg)
    assert "1-on-1 meeting" in prompt
    assert "what's your status?" in prompt
    assert "working on task X" in prompt
    assert "tell me more" in prompt
    assert "Please respond:" in prompt


def test_build_conversation_prompt_product_planning(tmp_path, monkeypatch):
    """PRODUCT conversations must inject product context + planning instructions."""
    from onemancompany.core import product as prod
    from onemancompany.core.conversation_adapters import _build_conversation_prompt

    monkeypatch.setattr(prod, "PRODUCTS_DIR", tmp_path)
    product = prod.create_product(name="OMC Website", owner_id="00002", description="官网")
    slug = product["slug"]
    prod.add_key_result(slug, title="DAU=1000", target=1000, unit="users")
    prod.create_issue(slug=slug, title="加载慢", description="...", created_by="00002")

    conv = Conversation(
        id="cp1", type="product", phase="active",
        employee_id="00002", tools_enabled=True,
        metadata={"product_slug": slug, "product_id": product["id"]},
        created_at="2026-03-18T10:00:00",
    )
    new_msg = Message(sender="ceo", role="CEO", text="开始规划", timestamp="t1")
    prompt = _build_conversation_prompt(conv, [], new_msg)

    # Must contain product context
    assert "OMC Website" in prompt
    assert slug in prompt
    # Must contain planning instructions referencing the tools
    assert "create_product_issue" in prompt
    assert "add_product_key_result" in prompt
    # Must not look like 1-on-1
    assert "1-on-1 meeting" not in prompt


def test_build_conversation_prompt_ceo_inbox():
    from onemancompany.core.conversation_adapters import _build_conversation_prompt

    conv = Conversation(
        id="c2", type="ceo_inbox", phase="active",
        employee_id="00100", tools_enabled=False,
        created_at="2026-03-18T10:00:00",
    )
    new_msg = Message(sender="ceo", role="CEO", text="approved", timestamp="t1")

    prompt = _build_conversation_prompt(conv, [], new_msg)
    assert "responding to your request" in prompt
    assert "1-on-1 meeting" not in prompt


def test_build_conversation_prompt_includes_attachment_read_instructions():
    """New CEO message with attachments must tell the agent to read() each file."""
    import json
    from onemancompany.core.conversation_adapters import _build_conversation_prompt

    conv = Conversation(
        id="c3", type="oneonone", phase="active",
        employee_id="00100", tools_enabled=True,
        created_at="2026-03-18T10:00:00",
    )
    attachment_path = "/tmp/uploads/spec.pdf"
    new_msg = Message(
        sender="ceo", role="CEO",
        text="please review this",
        timestamp="t1",
        attachments=[attachment_path],
    )

    prompt = _build_conversation_prompt(conv, [], new_msg)
    assert "please review this" in prompt
    assert "CEO attached the following files" in prompt
    assert attachment_path in prompt
    assert f"read({json.dumps(attachment_path)})" in prompt


def test_build_conversation_prompt_without_attachments_unchanged():
    """Empty attachments list keeps the prompt free of attachment boilerplate."""
    from onemancompany.core.conversation_adapters import _build_conversation_prompt

    conv = Conversation(
        id="c4", type="oneonone", phase="active",
        employee_id="00100", tools_enabled=True,
        created_at="2026-03-18T10:00:00",
    )
    new_msg = Message(sender="ceo", role="CEO", text="hi", timestamp="t1", attachments=[])

    prompt = _build_conversation_prompt(conv, [], new_msg)
    assert "CEO attached" not in prompt
    assert "read(" not in prompt


# ---------------------------------------------------------------------------
# LangChainAdapter / ClaudeSessionAdapter tests
# ---------------------------------------------------------------------------


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
    _, ctx = mock_executor.execute.await_args.args
    assert ctx.employee_id == "00100"
    assert ctx.work_dir.endswith("/employees/00100/workspace")


def test_get_executor_type_unknown_class_defaults_to_langchain():
    """Unknown executor class should default to langchain with a warning."""
    from onemancompany.core.conversation_adapters import _get_executor_type

    class UnknownExecutor:
        pass

    mock_mgr = MagicMock()
    mock_mgr.executors = {"00100": UnknownExecutor()}
    with patch("onemancompany.core.vessel.employee_manager", mock_mgr):
        assert _get_executor_type("00100") == "langchain"


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
    _, ctx = mock_executor.execute.await_args.args
    assert ctx.employee_id == "00100"
    assert ctx.work_dir.endswith("/employees/00100/workspace")
