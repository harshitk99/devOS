import asyncio
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.events import bus

router = APIRouter(tags=["live"])


@router.websocket("/ws/projects/{project_id}")
async def project_stream(websocket: WebSocket, project_id: UUID):
    """Live progress / log messages for a project. Long-running plans run in the background and
    report here so the UI never blocks. Recent history is replayed on connect."""
    await websocket.accept()
    queue, history = bus.subscribe(project_id, asyncio.get_running_loop())

    async def send_events() -> None:
        for message in history:
            await websocket.send_json(message)
        while True:
            await websocket.send_json(await queue.get())

    async def watch_disconnect() -> None:
        while True:
            await websocket.receive_text()  # raises WebSocketDisconnect when the client leaves

    tasks = [asyncio.create_task(send_events()), asyncio.create_task(watch_disconnect())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        bus.unsubscribe(project_id, queue)
