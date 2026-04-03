"""WebSocket streaming — live job events to clients."""

import asyncio

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/jobs/{job_id}/events")
async def job_events(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    queue: asyncio.Queue[dict] = asyncio.Queue()
    subscribers = websocket.app.state.event_subscribers
    if job_id not in subscribers:
        subscribers[job_id] = []
    subscribers[job_id].append(queue)
    logger.info("ws_connected", job_id=job_id)

    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.info("ws_disconnected", job_id=job_id)
    except Exception:
        logger.exception("ws_error", job_id=job_id)
    finally:
        if job_id in subscribers:
            subscribers[job_id] = [q for q in subscribers[job_id] if q is not queue]
            if not subscribers[job_id]:
                del subscribers[job_id]
