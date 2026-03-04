"""Unit tests for api/websocket.py — WebSocket connection manager."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# WebSocketManager
# ---------------------------------------------------------------------------


class TestWebSocketManagerConnect:
    async def test_connect_accepts_and_adds(self):
        with patch("onemancompany.api.websocket.company_state") as mock_state:
            mock_state.to_json.return_value = {"employees": []}
            from onemancompany.api.websocket import WebSocketManager

            mgr = WebSocketManager()
            ws = AsyncMock()

            await mgr.connect(ws)

            ws.accept.assert_called_once()
            assert ws in mgr.connections
            ws.send_json.assert_called_once()
            sent_data = ws.send_json.call_args[0][0]
            assert sent_data["type"] == "state_snapshot"
            assert sent_data["agent"] == "system"

    async def test_connect_multiple(self):
        with patch("onemancompany.api.websocket.company_state") as mock_state:
            mock_state.to_json.return_value = {}
            from onemancompany.api.websocket import WebSocketManager

            mgr = WebSocketManager()
            ws1 = AsyncMock()
            ws2 = AsyncMock()

            await mgr.connect(ws1)
            await mgr.connect(ws2)

            assert len(mgr.connections) == 2


class TestWebSocketManagerDisconnect:
    def test_disconnect_removes(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws = MagicMock()
        mgr.connections.add(ws)

        mgr.disconnect(ws)
        assert ws not in mgr.connections

    def test_disconnect_nonexistent_no_error(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws = MagicMock()
        mgr.disconnect(ws)  # Should not raise
        assert len(mgr.connections) == 0


class TestWebSocketManagerBroadcast:
    async def test_broadcast_sends_to_all(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        mgr.connections = {ws1, ws2}

        message = {"type": "test", "data": "hello"}
        await mgr.broadcast(message)

        ws1.send_json.assert_called_once_with(message)
        ws2.send_json.assert_called_once_with(message)

    async def test_broadcast_removes_dead_connections(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws_alive = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_json.side_effect = Exception("Connection closed")
        mgr.connections = {ws_alive, ws_dead}

        await mgr.broadcast({"type": "test"})

        assert ws_alive in mgr.connections
        assert ws_dead not in mgr.connections
        assert len(mgr.connections) == 1

    async def test_broadcast_empty_connections(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        await mgr.broadcast({"type": "test"})  # Should not raise

    async def test_broadcast_all_dead(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws1 = AsyncMock()
        ws1.send_json.side_effect = Exception("dead")
        ws2 = AsyncMock()
        ws2.send_json.side_effect = Exception("dead")
        mgr.connections = {ws1, ws2}

        await mgr.broadcast({"type": "test"})
        assert len(mgr.connections) == 0


class TestEventBroadcaster:
    async def test_broadcasts_events_from_bus(self):
        from onemancompany.core.events import CompanyEvent, EventBus
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        mgr.connections = {ws}

        bus = EventBus()

        with patch("onemancompany.api.websocket.event_bus", bus), \
             patch("onemancompany.api.websocket.company_state") as mock_state:
            mock_state.to_json.return_value = {"employees": []}

            # Start the broadcaster in a task
            task = asyncio.create_task(mgr.event_broadcaster())

            # Give it time to subscribe
            await asyncio.sleep(0.01)

            # Publish an event
            await bus.publish(CompanyEvent(type="agent_done", payload={"role": "HR"}, agent="HR"))

            # Give it time to process
            await asyncio.sleep(0.01)

            # Cancel the broadcaster
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Verify the websocket received the broadcast
            ws.send_json.assert_called()
            sent = ws.send_json.call_args[0][0]
            assert sent["type"] == "agent_done"
            assert sent["agent"] == "HR"

    async def test_unsubscribes_on_cancel(self):
        from onemancompany.core.events import EventBus
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        bus = EventBus()

        with patch("onemancompany.api.websocket.event_bus", bus), \
             patch("onemancompany.api.websocket.company_state") as mock_state:
            mock_state.to_json.return_value = {}

            task = asyncio.create_task(mgr.event_broadcaster())
            await asyncio.sleep(0.01)
            assert len(bus._subscribers) == 1

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            assert len(bus._subscribers) == 0
