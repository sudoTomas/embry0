"""Outbound webhook notifier (RAV-1052) — signing, envelope, retry, isolation."""

import asyncio
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock

import httpx

from embry0.notifications.webhook import WebhookNotifier, sign_plain, sign_v2

URL = "http://receiver.test/webhook"
SECRET = "s3cret"


def _repo(url=URL, secret=SECRET):
    return MagicMock(get_webhook_credentials=AsyncMock(return_value=(url, secret)))


def _notifier(handler, repo=None, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kwargs.setdefault("backoff_seconds", 0.0)
    return WebhookNotifier(repo or _repo(), http_client=client, **kwargs)


def _capture(status_codes):
    """Handler serving the given status codes in order (last repeats)."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status = status_codes[min(len(seen) - 1, len(status_codes) - 1)]
        return httpx.Response(status, json={"ok": status < 300})

    handler.seen = seen
    return handler


def test_sign_v2_matches_receiver_side_recomputation():
    body = b'{"event":"job.completed"}'
    ts = "1753500000"
    expected = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert sign_v2(SECRET, ts, body) == expected


def test_sign_plain_covers_body_only():
    body = b"payload"
    assert sign_plain(SECRET, body) == hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


async def test_emit_delivers_signed_envelope():
    handler = _capture([200])
    notifier = _notifier(handler)
    assert await notifier.emit("job.completed", {"job_id": "j1"}) is True

    request = handler.seen[0]
    assert str(request.url) == URL
    assert request.headers["Content-Type"] == "application/json"

    body = request.content
    envelope = json.loads(body)
    assert envelope["event"] == "job.completed"
    assert envelope["data"] == {"job_id": "j1"}
    assert "ts" in envelope

    ts = request.headers["X-Webhook-Timestamp"]
    assert abs(int(ts) - int(time.time())) < 300
    assert request.headers["X-Webhook-Signature-V2"] == sign_v2(SECRET, ts, body)
    assert request.headers["X-Webhook-Signature"] == sign_plain(SECRET, body)


async def test_emit_skips_when_unconfigured():
    handler = _capture([200])
    notifier = _notifier(handler, repo=_repo(url="", secret=""))
    assert await notifier.emit("job.completed", {}) is False
    assert handler.seen == []


async def test_emit_unsigned_when_secret_missing():
    handler = _capture([200])
    notifier = _notifier(handler, repo=_repo(secret=""))
    assert await notifier.emit("job.completed", {}) is True
    request = handler.seen[0]
    assert "X-Webhook-Signature-V2" not in request.headers
    assert "X-Webhook-Signature" not in request.headers
    assert "X-Webhook-Timestamp" in request.headers


async def test_retry_then_success():
    handler = _capture([500, 200])
    notifier = _notifier(handler)
    assert await notifier.emit("watcher.proposed", {}) is True
    assert len(handler.seen) == 2


async def test_gives_up_after_max_attempts():
    handler = _capture([503])
    notifier = _notifier(handler, max_attempts=3)
    assert await notifier.emit("job.failed", {}) is False
    assert len(handler.seen) == 3


async def test_connect_error_never_raises():
    def handler(request):
        raise httpx.ConnectError("receiver down")

    notifier = _notifier(handler)
    assert await notifier.emit("job.completed", {}) is False


async def test_config_read_failure_never_raises():
    repo = MagicMock(get_webhook_credentials=AsyncMock(side_effect=RuntimeError("db down")))
    notifier = _notifier(_capture([200]), repo=repo)
    assert await notifier.emit("job.completed", {}) is False


async def test_emit_soon_is_fire_and_forget():
    handler = _capture([200])
    notifier = _notifier(handler)
    notifier.emit_soon("agent.question", {"job_id": "j1"})
    for _ in range(20):
        if handler.seen:
            break
        await asyncio.sleep(0.01)
    assert len(handler.seen) == 1
    assert notifier._tasks == set()  # done-callback pruned the task


async def test_non_serializable_data_falls_back_to_str():
    handler = _capture([200])
    notifier = _notifier(handler)
    assert await notifier.emit("job.completed", {"when": object()}) is True
    envelope = json.loads(handler.seen[0].content)
    assert isinstance(envelope["data"]["when"], str)


async def test_aclose_cancels_pending_and_closes_client():
    started = asyncio.Event()

    async def slow_handler(request):
        started.set()
        await asyncio.sleep(30)
        return httpx.Response(200)

    notifier = _notifier(slow_handler)
    notifier.emit_soon("job.completed", {})
    await asyncio.wait_for(started.wait(), timeout=2)
    await notifier.aclose()
    assert notifier._tasks == set()


async def test_honours_retry_after_on_429():
    """#252: a rate-limited receiver's own delay wins over the backoff curve."""
    delays = []

    async def record(delay):
        delays.append(delay)

    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    notifier = _notifier(handler, backoff_seconds=10.0, sleep=record)
    assert await notifier.emit("job.completed", {}) is True
    assert delays == [2.0]


async def test_every_attempt_of_one_delivery_carries_its_one_delivery_id():
    """#613: an attempt answered 5xx may have been taken, so a retry can repeat a delivery. Every attempt of one
    event carries the same ``delivery`` id, in the signed body and as a header, so the receiver drops a repeat; two
    events carry two ids."""
    handler = _capture([502, 503, 200])
    notifier = _notifier(handler)
    assert await notifier.emit("job.completed", {"job_id": "j1"}) is True
    assert len(handler.seen) == 3
    ids = {json.loads(request.content)["delivery"] for request in handler.seen}
    headers = {request.headers["X-Webhook-Delivery"] for request in handler.seen}
    assert len(ids) == 1 and ids == headers, (ids, headers)
    assert len(next(iter(ids))) == 32, "a uuid4's hex"

    await notifier.emit("job.failed", {"job_id": "j2"})
    assert json.loads(handler.seen[-1].content)["delivery"] not in ids, "a second event reuses the first's id"
