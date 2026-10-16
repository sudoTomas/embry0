"""WebSocket streaming — live job events to clients."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from embry0.storage.repositories.jobs import TERMINAL_JOB_STATUSES

logger = structlog.get_logger(__name__)
router = APIRouter()

#: RFC 6455 normal closure: the server finished the stream on purpose. The
#: client treats it as "complete" and does not reconnect (spec 3.5).
FINISHED_CLOSE_CODE = 1000


def _is_job_finished(event: Any) -> bool:
    return isinstance(event, dict) and event.get("type") == "job_finished"


async def _job_is_terminal(jobs_repo: Any, job_id: str) -> bool:
    """True when the job row says the job ended: covers a job finished before
    job_finished existed and a job_finished whose persist failed."""
    try:
        row = await jobs_repo.get(job_id)
    except Exception:
        return False
    return isinstance(row, dict) and str(row.get("status") or "") in TERMINAL_JOB_STATUSES


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


REPLAY_PAGE = 500


async def _replay_rows(jobs_repo: Any, job_id: str, since_seq: int) -> AsyncIterator[dict[str, Any]]:
    """Every persisted event row newer than ``since_seq``, page by page.

    One ``get_log_events`` call stops at its limit, which left a long job's
    newer events out of the replay (#203).
    """
    cursor = since_seq
    while True:
        rows = await jobs_repo.get_log_events(job_id, since_seq=cursor, limit=REPLAY_PAGE)
        for row in rows:
            yield row
        last = rows[-1].get("id") if rows else None
        if len(rows) < REPLAY_PAGE or not isinstance(last, int) or last <= cursor:
            return
        cursor = last


async def _wait_for_disconnect(websocket: WebSocket) -> None:
    """Return once the client has gone. Anything it sends is ignored.

    A receive that fails means the socket is unusable, which counts as gone.
    """
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
    except Exception:
        return


@router.websocket("/ws/jobs/{job_id}/events")
async def job_events(websocket: WebSocket, job_id: str) -> None:
    config = websocket.app.state.config

    # Auth via Sec-WebSocket-Protocol subprotocol header. Client sends:
    #   Sec-WebSocket-Protocol: embry0.token.<scoped_token>
    # where the token comes from POST /api/v1/jobs/{job_id}/events-token
    # (normal Bearer auth) and is scoped to this one job with a short TTL —
    # the raw API key never rides the subprotocol anymore (RAV-1070, same
    # rationale as the SSE scoped token, RAV-1060). The server validates and
    # echoes the matched subprotocol back on accept (RFC 6455 requires it).
    # RAV-1069: an SSO session cookie (operator+) also authenticates the
    # handshake — browsers send cookies on same-origin WS upgrades for free.
    # #518: only AUTH_DEV_MODE skips this. An empty API_KEY does not, and a
    # token signed with empty material never verifies (scoped_tokens).
    matched_subprotocol: str | None = None
    if not config.auth_dev_mode:
        session_ok = False
        if config.sso_enabled:
            from embry0.api.deps import validate_session_user
            from embry0.api.session import TIER_ADMIN, TIER_OPERATOR, request_session

            session = request_session(websocket)
            session_ok = (
                session is not None
                and session.tier in (TIER_ADMIN, TIER_OPERATOR)
                and await validate_session_user(websocket.app, session)
            )
        from embry0.api.scoped_tokens import signing_material, verify_scoped_token

        subprotocols = websocket.scope.get("subprotocols", [])
        for sp in subprotocols:
            if sp.startswith("embry0.token."):
                presented = sp[len("embry0.token.") :]
                if verify_scoped_token(signing_material(config), f"ws:{job_id}", presented):
                    matched_subprotocol = sp
                    break
        if matched_subprotocol is None and not session_ok:
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
    replayed_finished = False
    client_gone: asyncio.Task[Any] | None = None
    next_event: asyncio.Task[Any] | None = None
    try:
        # None when the app has no repo: the replay warns and the live loop runs.
        jobs_repo = getattr(websocket.app.state, "jobs_repo", None)
        try:
            async for event in _replay_rows(jobs_repo, job_id, since_seq):
                payload = event.get("payload", event)
                if not isinstance(payload, dict):
                    continue
                # Ensure replayed events carry their seq for the client's next
                # reconnect (the DB row id is the seq).
                row_id = event.get("id")
                if isinstance(row_id, int):
                    payload = {**payload, "event_seq": row_id}
                    if row_id > watermark:
                        watermark = row_id
                if _is_job_finished(payload):
                    replayed_finished = True
                if not _passes_filter(payload, wanted_types):
                    continue
                await websocket.send_json(payload)
        except Exception:
            logger.warning("event_replay_failed", job_id=job_id, exc_info=True)

        # A finished job has nothing live to wait for: close after the replay.
        if replayed_finished or await _job_is_terminal(jobs_repo, job_id):
            logger.info("ws_closed_finished_job", job_id=job_id, replayed_terminal=replayed_finished)
            await websocket.close(code=FINISHED_CLOSE_CODE)
            return

        # Live loop — skip any event whose seq is <= watermark (already
        # replayed). Watch the socket alongside the queue: a client that
        # leaves while the job is quiet must not stay subscribed until the
        # next event (#505).
        client_gone = asyncio.create_task(_wait_for_disconnect(websocket))
        while True:
            next_event = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({next_event, client_gone}, return_when=asyncio.FIRST_COMPLETED)
            if client_gone in done:
                logger.info("ws_disconnected", job_id=job_id)
                break
            event = next_event.result()
            event_seq = event.get("event_seq")
            if isinstance(event_seq, int) and event_seq <= watermark:
                continue  # already delivered via replay
            # The terminal event is checked before the filter: a client that
            # filters it out still has its socket closed (review focus 2).
            finished = _is_job_finished(event)
            if _passes_filter(event, wanted_types):
                await websocket.send_json(event)
            if finished:
                logger.info("ws_closed_finished_job", job_id=job_id, replayed_terminal=False)
                await websocket.close(code=FINISHED_CLOSE_CODE)
                break
    except WebSocketDisconnect:
        logger.info("ws_disconnected", job_id=job_id)
    except Exception:
        logger.exception("ws_error", job_id=job_id)
    finally:
        if client_gone is not None:
            client_gone.cancel()
        if next_event is not None:
            next_event.cancel()
        await event_bus.unsubscribe(job_id, queue)
