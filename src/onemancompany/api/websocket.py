"""WebSocket connection manager — broadcasts company events to all connected clients."""

from __future__ import annotations

import asyncio

from fastapi import WebSocket

from onemancompany.core.events import CompanyEvent, event_bus


class WebSocketManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)
        # Tell frontend to bootstrap from REST API
        await ws.send_json({
            "type": "connected",
            "payload": {"message": "Bootstrap from REST API"},
        })

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead: set[WebSocket] = set()
        for ws in self.connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self.connections -= dead

    async def event_broadcaster(self) -> None:
        """Background task: forward events to WebSocket clients (no full state)."""
        queue = event_bus.subscribe()
        try:
            while True:
                event: CompanyEvent = await queue.get()
                # Real-time events forwarded directly (chat, popups, etc.)
                # Full state is NOT attached — frontend fetches from REST on tick
                await self.broadcast({
                    "type": event.type,
                    "agent": event.agent,
                    "payload": event.payload,
                })
        except asyncio.CancelledError:
            raise
        finally:
            event_bus.unsubscribe(queue)


ws_manager = WebSocketManager()
