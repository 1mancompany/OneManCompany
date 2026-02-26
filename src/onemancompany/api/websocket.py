"""WebSocket connection manager — broadcasts company events to all connected clients."""

from __future__ import annotations

import asyncio

from fastapi import WebSocket

from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state


class WebSocketManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)
        # Send current state snapshot on connect
        await ws.send_json({
            "type": "state_snapshot",
            "agent": "system",
            "payload": {},
            "state": company_state.to_json(),
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
        """Background task: subscribe to event bus and broadcast to WebSocket clients."""
        queue = event_bus.subscribe()
        try:
            while True:
                event: CompanyEvent = await queue.get()
                await self.broadcast({
                    "type": event.type,
                    "agent": event.agent,
                    "payload": event.payload,
                    "state": company_state.to_json(),
                })
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(queue)


ws_manager = WebSocketManager()
