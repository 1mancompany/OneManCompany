"""OneManCompany — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from onemancompany.api.routes import router
from onemancompany.api.websocket import ws_manager

FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Eagerly load equipment (meeting rooms, tools) into company_state
    from onemancompany.agents.coo_agent import _load_equipment_from_disk
    _load_equipment_from_disk()

    # Start background WebSocket event broadcaster
    broadcaster_task = asyncio.create_task(ws_manager.event_broadcaster())
    print(f"🏢 One Man Company HQ is open!")
    print(f"   Frontend: http://localhost:{app.state.port if hasattr(app.state, 'port') else 8000}")
    yield
    broadcaster_task.cancel()
    try:
        await broadcaster_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="One Man Company", lifespan=lifespan)
app.include_router(router)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def run() -> None:
    from onemancompany.core.config import settings

    app.state.port = settings.port
    uvicorn.run(
        "onemancompany.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )


if __name__ == "__main__":
    run()
