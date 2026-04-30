"""WebSocket streaming — live job events to clients."""

import hmac

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = structlog.get_logger(__name__)
router = APIRouter()


def _passes_filter(event: dict[str, object], wanted: set[str]) -> bool:
    """Return True if the event should be forwarded given the wanted type set.

    Empty ``wanted`` means "no filter" (everything passes). An event whose
    ``type`` is missing or unknown is dropped when a filter is active — the
    only way to receive untyped events is to omit the filter entirely.
    """
    if not wanted:
        return True
    event_type = event.get("type") if isinstance(event, dict) else None
    return event_type in wanted


@router.websocket("/ws/jobs/{job_id}/events")
async def job_events(websocket: WebSocket, job_id: str) -> None:
    config = websocket.app.state.config

    # Auth via Sec-WebSocket-Protocol subprotocol header. Client sends:
    #   Sec-WebSocket-Protocol: athanor.bearer.<api_key>
    # Server validates with hmac.compare_digest and echoes the matched
    # subprotocol back on accept (RFC 6455 requires the echo).
    matched_subprotocol: str | None = None
    if config.api_key and not config.auth_dev_mode:
        subprotocols = websocket.scope.get("subprotocols", [])
        for sp in subprotocols:
            if sp.startswith("athanor.bearer."):
                presented = sp[len("athanor.bearer.") :]
                if hmac.compare_digest(presented, config.api_key):
                    matched_subprotocol = sp
                    break
        if matched_subprotocol is None:
            logger.warning(
                "ws_unauthorized",
                job_id=job_id,
                remote=websocket.client.host if websocket.client else "unknown",
            )
            await websocket.close(code=4001, reason="Unauthorized")
            return

    await websocket.accept(subprotocol=matched_subprotocol)

    # Parse the replay cursor. Clients resuming a dropped connection pass
    # ``?since_seq=<last_event_seq>``; fresh clients pass 0 or omit it.
    try:
        since_seq = int(websocket.query_params.get("since_seq", "0"))
    except ValueError:
        since_seq = 0

    # Optional server-side filter: ``?event_types=progress,cost_update`` sends
    # only events whose ``type`` is in the CSV set. Empty / missing = no filter.
    raw_types = websocket.query_params.get("event_types", "")
    wanted_types = {t.strip() for t in raw_types.split(",") if t.strip()}

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
                if not _passes_filter(payload, wanted_types):
                    continue
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
            if not _passes_filter(event, wanted_types):
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        logger.info("ws_disconnected", job_id=job_id)
    except Exception:
        logger.exception("ws_error", job_id=job_id)
    finally:
        await event_bus.unsubscribe(job_id, queue)
