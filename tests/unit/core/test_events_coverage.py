"""Coverage tests for core/events.py — open_popup function (lines 61-70)."""

from __future__ import annotations

import pytest

from onemancompany.core.events import CompanyEvent, EventBus, open_popup


@pytest.mark.asyncio
async def test_open_popup_basic():
    """open_popup publishes an OPEN_POPUP event with basic args."""
    from onemancompany.core.events import event_bus

    q = event_bus.subscribe()
    try:
        await open_popup("Test Title", "Hello", agent="HR")
        event = q.get_nowait()
        assert event.payload["type"] == "info"
        assert event.payload["title"] == "Test Title"
        assert event.payload["message"] == "Hello"
        assert event.payload["agent"] == "HR"
        assert "url" not in event.payload
        assert "buttons" not in event.payload
    finally:
        event_bus.unsubscribe(q)


@pytest.mark.asyncio
async def test_open_popup_with_url():
    """open_popup includes url when provided."""
    from onemancompany.core.events import event_bus

    q = event_bus.subscribe()
    try:
        await open_popup("URL Popup", url="https://example.com", popup_type="url")
        event = q.get_nowait()
        assert event.payload["url"] == "https://example.com"
        assert event.payload["type"] == "url"
    finally:
        event_bus.unsubscribe(q)


@pytest.mark.asyncio
async def test_open_popup_with_buttons():
    """open_popup includes buttons when provided."""
    from onemancompany.core.events import event_bus

    q = event_bus.subscribe()
    try:
        buttons = [{"label": "OK", "primary": True}]
        await open_popup("Btn Popup", buttons=buttons)
        event = q.get_nowait()
        assert event.payload["buttons"] == buttons
    finally:
        event_bus.unsubscribe(q)


@pytest.mark.asyncio
async def test_open_popup_confirm_type():
    """open_popup with confirm type includes confirm_label and callback_url."""
    from onemancompany.core.events import event_bus

    q = event_bus.subscribe()
    try:
        await open_popup(
            "Confirm?", popup_type="confirm",
            confirm_label="Yes!", callback_url="/api/confirm",
        )
        event = q.get_nowait()
        assert event.payload["type"] == "confirm"
        assert event.payload["confirm_label"] == "Yes!"
        assert event.payload["callback_url"] == "/api/confirm"
    finally:
        event_bus.unsubscribe(q)


@pytest.mark.asyncio
async def test_open_popup_confirm_without_callback():
    """open_popup confirm type without callback_url omits it."""
    from onemancompany.core.events import event_bus

    q = event_bus.subscribe()
    try:
        await open_popup("Confirm no cb", popup_type="confirm")
        event = q.get_nowait()
        assert event.payload["type"] == "confirm"
        assert event.payload["confirm_label"] == "Confirm"
        assert "callback_url" not in event.payload
    finally:
        event_bus.unsubscribe(q)
