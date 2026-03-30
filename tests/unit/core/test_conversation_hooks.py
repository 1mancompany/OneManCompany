"""Tests for conversation close hooks registry."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.conversation import Conversation
from onemancompany.core.conversation_hooks import (
    register_close_hook,
    run_close_hook,
)


@pytest.mark.asyncio
async def test_run_close_hook_dispatches_by_type():
    results = {}

    @register_close_hook("test_hook_type")
    async def _test_hook(conv):
        results["called"] = True
        results["conv_id"] = conv.id
        return {"status": "done"}

    conv = Conversation(
        id="c1", type="test_hook_type", phase="closing",
        employee_id="00100", tools_enabled=False,
        created_at="2026-03-18T10:00:00",
    )
    result = await run_close_hook(conv, wait=True)
    assert results["called"] is True
    assert result == {"status": "done"}


@pytest.mark.asyncio
async def test_run_close_hook_unknown_type_returns_none():
    conv = Conversation(
        id="c2", type="totally_unknown_type_xyz", phase="closing",
        employee_id="00100", tools_enabled=False,
        created_at="2026-03-18T10:00:00",
    )
    result = await run_close_hook(conv, wait=True)
    assert result is None


@pytest.mark.asyncio
async def test_oneonone_hook_returns_reflection():
    from onemancompany.core.conversation_hooks import _close_oneonone

    conv = Conversation(
        id="c4", type="oneonone", phase="closing",
        employee_id="00100", tools_enabled=True,
        metadata={},
        created_at="2026-03-18T10:00:00",
    )
    result = await _close_oneonone(conv)
    assert result is not None
    assert "reflection" in result
    assert "principles_updated" in result


@pytest.mark.asyncio
async def test_run_close_hook_fire_and_forget():
    """wait=False should create a background task and return None."""
    called = {}

    @register_close_hook("fire_forget_type")
    async def _ff_hook(conv):
        called["done"] = True
        return {"ignored": True}

    conv = Conversation(
        id="c5", type="fire_forget_type", phase="closing",
        employee_id="00100", tools_enabled=False,
        created_at="2026-03-18T10:00:00",
    )
    result = await run_close_hook(conv, wait=False)
    assert result is None
    # Give the background task a chance to run
    import asyncio
    await asyncio.sleep(0.05)
    assert called.get("done") is True


@pytest.mark.asyncio
async def test_fire_and_forget_hook_exception_is_logged():
    """wait=False hooks that raise should log, not crash."""

    @register_close_hook("error_hook_type")
    async def _error_hook(conv):
        raise RuntimeError("hook boom")

    conv = Conversation(
        id="c6", type="error_hook_type", phase="closing",
        employee_id="00100", tools_enabled=False,
        created_at="2026-03-18T10:00:00",
    )
    # Should not raise — error is caught by _run_hook_safe wrapper
    result = await run_close_hook(conv, wait=False)
    assert result is None
    import asyncio
    await asyncio.sleep(0.05)  # let background task complete


# ---------------------------------------------------------------------------
# 1-on-1 reflection parsing tests
# ---------------------------------------------------------------------------

def _make_oneonone_conv(employee_id="00100"):
    return Conversation(
        id="conv-reflect", type="oneonone", phase="closing",
        employee_id=employee_id, tools_enabled=True,
        metadata={}, created_at="2026-03-18T10:00:00",
    )


@dataclass
class FakeLLMResult:
    content: str


# Patches at the source module level (lazy imports resolve from these modules)
_PATCHES = {
    "make_llm": "onemancompany.agents.base.make_llm",
    "llm_invoke": "onemancompany.core.llm_utils.llm_invoke_with_retry",
    "store": "onemancompany.core.store",
    "event_bus": "onemancompany.core.events.event_bus",
    "load_emp": "onemancompany.core.store.load_employee",
    "conv_dir": "onemancompany.core.conversation_hooks.resolve_conv_dir",
    "load_msgs": "onemancompany.core.conversation_hooks.load_messages",
}


def _setup_store_mock(mock_store):
    """Configure mock store with common async methods."""
    mock_store.save_work_principles = AsyncMock()
    mock_store.load_employee_guidance = MagicMock(return_value=[])
    mock_store.save_guidance = AsyncMock()
    mock_store.save_employee_runtime = AsyncMock()
    mock_store.load_employee = MagicMock()


class TestOneononeReflectionParsing:
    """Tests for the LLM response parsing in _close_oneonone."""

    @pytest.mark.asyncio
    async def test_updated_principles_and_summary(self, tmp_path):
        """LLM returns UPDATED + SUMMARY → both saved."""
        llm_response = "UPDATED:\n- Always write tests first\n- Prioritize quality\nSUMMARY:\nCEO emphasized TDD approach."
        fake_result = FakeLLMResult(content=llm_response)
        mock_msg = MagicMock(role="ceo", text="Focus on testing")

        with patch(_PATCHES["conv_dir"], return_value=tmp_path), \
             patch(_PATCHES["load_msgs"], return_value=[mock_msg]), \
             patch(_PATCHES["make_llm"], return_value=MagicMock()), \
             patch(_PATCHES["llm_invoke"], new_callable=AsyncMock, return_value=fake_result), \
             patch(_PATCHES["store"]) as mock_store, \
             patch(_PATCHES["event_bus"]) as mock_bus, \
             patch(_PATCHES["load_emp"], return_value={"name": "Alice", "nickname": "A", "role": "Dev", "department": "Tech"}):

            _setup_store_mock(mock_store)
            mock_bus.publish = AsyncMock()

            from onemancompany.core.conversation_hooks import _close_oneonone
            result = await _close_oneonone(_make_oneonone_conv())

        assert result["principles_updated"] is True
        assert result["note_saved"] is True

    @pytest.mark.asyncio
    async def test_no_update_with_summary(self, tmp_path):
        """LLM returns NO_UPDATE + SUMMARY → only note saved."""
        llm_response = "NO_UPDATE\nSUMMARY:\nRoutine check-in, no action items."
        fake_result = FakeLLMResult(content=llm_response)
        mock_msg = MagicMock(role="ceo", text="Good job")

        with patch(_PATCHES["conv_dir"], return_value=tmp_path), \
             patch(_PATCHES["load_msgs"], return_value=[mock_msg]), \
             patch(_PATCHES["make_llm"], return_value=MagicMock()), \
             patch(_PATCHES["llm_invoke"], new_callable=AsyncMock, return_value=fake_result), \
             patch(_PATCHES["store"]) as mock_store, \
             patch(_PATCHES["event_bus"]) as mock_bus, \
             patch(_PATCHES["load_emp"], return_value={"name": "Bob", "nickname": "B", "role": "PM", "department": "Product"}):

            _setup_store_mock(mock_store)
            mock_bus.publish = AsyncMock()

            from onemancompany.core.conversation_hooks import _close_oneonone
            result = await _close_oneonone(_make_oneonone_conv())

        assert result["principles_updated"] is False
        assert result["note_saved"] is True

    @pytest.mark.asyncio
    async def test_no_messages_skips_reflection(self, tmp_path):
        """Empty conversation → skip reflection entirely."""
        with patch(_PATCHES["conv_dir"], return_value=tmp_path), \
             patch(_PATCHES["load_msgs"], return_value=[]), \
             patch(_PATCHES["store"]) as mock_store, \
             patch(_PATCHES["load_emp"], return_value={"name": "X", "nickname": "X", "role": "X", "department": "X"}):

            _setup_store_mock(mock_store)

            from onemancompany.core.conversation_hooks import _close_oneonone
            result = await _close_oneonone(_make_oneonone_conv())

        assert result["reflection"] == ""
        assert result["principles_updated"] is False
        assert result["note_saved"] is False

    @pytest.mark.asyncio
    async def test_inline_updated_not_false_positive(self, tmp_path):
        """LLM content with 'UPDATED:' mid-line should not trigger update
        when the actual marker says NO_UPDATE."""
        # UPDATED: appears inline but actual answer is NO_UPDATE
        llm_response = "I have UPDATED: the roadmap context.\nNO_UPDATE\nSUMMARY:\nBrief chat."
        fake_result = FakeLLMResult(content=llm_response)
        mock_msg = MagicMock(role="ceo", text="Check status")

        with patch(_PATCHES["conv_dir"], return_value=tmp_path), \
             patch(_PATCHES["load_msgs"], return_value=[mock_msg]), \
             patch(_PATCHES["make_llm"], return_value=MagicMock()), \
             patch(_PATCHES["llm_invoke"], new_callable=AsyncMock, return_value=fake_result), \
             patch(_PATCHES["store"]) as mock_store, \
             patch(_PATCHES["event_bus"]) as mock_bus, \
             patch(_PATCHES["load_emp"], return_value={"name": "Eve", "nickname": "E", "role": "Eng", "department": "Tech"}):

            _setup_store_mock(mock_store)
            mock_bus.publish = AsyncMock()

            from onemancompany.core.conversation_hooks import _close_oneonone
            result = await _close_oneonone(_make_oneonone_conv())

        assert result["principles_updated"] is False
