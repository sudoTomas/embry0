"""WebSocket streaming — live job events to clients."""

import hmac

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/jobs/{job_id}/events")
async def job_events(websocket: WebSocket, job_id: str) -> None:
    # Auth check
    config = websocket.app.state.config
    if not config.dev_mode and config.api_key:
        token = websocket.query_params.get("token", "")
        if not hmac.compare_digest(token, config.api_key):
            await websocket.close(code=4001, reason="Unauthorized")
            return
    await websocket.accept()

    # Parse the replay cursor. Clients resuming a dropped connection pass
    # ``?since_seq=<last_event_seq>``; fresh clients pass 0 or omit it.
    try:
        since_seq = int(websocket.query_params.get("since_seq", "0"))
    except ValueError:
        since_seq = 0

    event_bus = websocket.app.state.event_bus

    # Subscribe FIRST so we don't miss any events that land during replay.
    # We'll drop any live event whose seq is <= watermark (already replayed).
    queue = await event_bus.subscribe(job_id)
    logger.info("ws_connected", job_id=job_id, since_seq=since_seq)

    # Replay persisted events strictly newer than since_seq, tracking the
    # highest seq we send so we can dedupe the live stream below.
    watermark = since_seq
    try:
        jobs_repo = websocket.app.state.jobs_repo
        events = await jobs_repo.get_log_events(job_id, since_seq=since_seq)
        for event in events:
            payload = event.get("payload", event)
            if isinstance(payload, dict):
                # Ensure replayed events carry their seq for the client's next
                # reconnect (the DB row id is the seq).
                row_id = event.get("id")
                if isinstance(row_id, int):
                    payload = {**payload, "event_seq": row_id}
                    if row_id > watermark:
                        watermark = row_id
                await websocket.send_json(payload)
    except Exception:
        logger.warning("event_replay_failed", job_id=job_id, exc_info=True)

    # Live loop — skip any event whose seq is <= watermark (already replayed).
    try:
        while True:
            event = await queue.get()
            event_seq = event.get("event_seq")
            if isinstance(event_seq, int) and event_seq <= watermark:
                continue  # already delivered via replay
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.info("ws_disconnected", job_id=job_id)
    except Exception:
        logger.exception("ws_error", job_id=job_id)
    finally:
        await event_bus.unsubscribe(job_id, queue)
