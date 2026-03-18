"""Tests for conversation close hooks registry."""

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
async def test_ceo_inbox_hook_returns_summary():
    from onemancompany.core.conversation_hooks import _close_ceo_inbox

    conv = Conversation(
        id="c3", type="ceo_inbox", phase="closing",
        employee_id="00100", tools_enabled=False,
        metadata={"node_id": "node-1", "project_dir": "/tmp/test"},
        created_at="2026-03-18T10:00:00",
    )
    result = await _close_ceo_inbox(conv)
    assert result is not None
    assert "summary" in result
    assert result["node_id"] == "node-1"


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
