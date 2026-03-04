"""Unit tests for core/events.py — EventBus publish/subscribe."""

from __future__ import annotations

import asyncio

import pytest

from onemancompany.core.events import CompanyEvent, EventBus


# ---------------------------------------------------------------------------
# CompanyEvent
# ---------------------------------------------------------------------------

class TestCompanyEvent:
    def test_creation(self):
        event = CompanyEvent(type="employee_hired", payload={"name": "Test"}, agent="HR")
        assert event.type == "employee_hired"
        assert event.payload == {"name": "Test"}
        assert event.agent == "HR"

    def test_default_agent(self):
        event = CompanyEvent(type="state_snapshot", payload={})
        assert event.agent == "system"


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

class TestEventBus:
    def test_subscribe_returns_queue(self):
        bus = EventBus()
        q = bus.subscribe()
        assert isinstance(q, asyncio.Queue)

    def test_unsubscribe(self):
        bus = EventBus()
        q = bus.subscribe()
        assert len(bus._subscribers) == 1
        bus.unsubscribe(q)
        assert len(bus._subscribers) == 0

    def test_unsubscribe_nonexistent_no_error(self):
        bus = EventBus()
        q = asyncio.Queue()
        bus.unsubscribe(q)  # should not raise

    @pytest.mark.asyncio
    async def test_publish_delivers_to_subscriber(self):
        bus = EventBus()
        q = bus.subscribe()

        event = CompanyEvent(type="employee_hired", payload={"id": "00010"}, agent="HR")
        await bus.publish(event)

        received = q.get_nowait()
        assert received.type == "employee_hired"
        assert received.payload["id"] == "00010"

    @pytest.mark.asyncio
    async def test_publish_delivers_to_multiple_subscribers(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()

        event = CompanyEvent(type="state_snapshot", payload={})
        await bus.publish(event)

        assert not q1.empty()
        assert not q2.empty()
        assert q1.get_nowait().type == "state_snapshot"
        assert q2.get_nowait().type == "state_snapshot"

    @pytest.mark.asyncio
    async def test_unsubscribed_queue_does_not_receive(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)

        event = CompanyEvent(type="employee_fired", payload={})
        await bus.publish(event)

        assert q.empty()

    @pytest.mark.asyncio
    async def test_publish_multiple_events(self):
        bus = EventBus()
        q = bus.subscribe()

        await bus.publish(CompanyEvent(type="employee_hired", payload={"n": 1}))
        await bus.publish(CompanyEvent(type="employee_fired", payload={"n": 2}))

        e1 = q.get_nowait()
        e2 = q.get_nowait()
        assert e1.type == "employee_hired"
        assert e2.type == "employee_fired"
