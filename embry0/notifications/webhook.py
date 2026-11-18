"""Outbound event webhooks — signed POSTs to an external consumer (RAV-1052).

Signing matches the Hermes Agent's "Generic V2" webhook routes (RAV-1050), so
such a receiver needs zero glue: ``X-Webhook-Signature-V2`` is the
HMAC-SHA256 hex digest of ``"{timestamp}.{body}"`` and
``X-Webhook-Timestamp`` carries unix seconds (receivers enforce a ±300s replay
window, so the timestamp is re-stamped on every retry attempt). A legacy
``X-Webhook-Signature`` (HMAC of the body alone) rides along for other
consumers.

Delivery is best-effort fire-and-forget: ``emit_soon`` schedules a task and
returns immediately, each attempt is logged, and nothing here ever raises into
the pipeline. No delivery table in v1.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from embry0.notifications.retry import (
    SendError,
    TransientSendError,
    advertised_delay,
    send_with_retry,
)

logger = structlog.get_logger(__name__)


def sign_v2(secret: str, timestamp: str, body: bytes) -> str:
    """Hermes Generic V2: HMAC-SHA256 hex over ``{timestamp}.{body}``."""
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def sign_plain(secret: str, body: bytes) -> str:
    """Legacy scheme: HMAC-SHA256 hex over the body alone (no replay protection)."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookNotifier:
    """POSTs ``{event, ts, data}`` envelopes to the configured webhook URL."""

    def __init__(
        self,
        integration_repo: Any,
        *,
        http_client: httpx.AsyncClient | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        timeout: float = 10.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._integrations = integration_repo
        self._client = http_client
        self._owns_client = http_client is None
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._timeout = timeout
        self._sleep = sleep
        self._tasks: set[asyncio.Task[Any]] = set()

    def emit_soon(self, event: str, data: dict[str, Any]) -> None:
        """Schedule an emission without awaiting it. Safe to call from any
        pipeline path — delivery failures never propagate."""
        task = asyncio.create_task(self.emit(event, data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def emit(self, event: str, data: dict[str, Any]) -> bool:
        """Deliver one event. Returns True on 2xx; never raises."""
        try:
            url, secret = await self._integrations.get_webhook_credentials()
        except Exception:
            logger.warning("webhook_config_read_failed", webhook_event=event, exc_info=True)
            return False
        if not url:
            logger.debug("webhook_skipped_unconfigured", webhook_event=event)
            return False

        # One id for the delivery, the same on every attempt (#613): an attempt answered 5xx or whose answer was
        # lost may have been taken, so a retry can repeat a delivery, and the receiver drops one it has seen. In
        # the body, so the signature covers it, and in a header.
        delivery = uuid.uuid4().hex
        envelope = {"event": event, "delivery": delivery, "ts": datetime.now(UTC).isoformat(), "data": data}
        body = json.dumps(envelope, separators=(",", ":"), default=str).encode()

        async def attempt(number: int) -> bool:
            headers = {"Content-Type": "application/json", "X-Webhook-Delivery": delivery}
            # Fresh timestamp per attempt so retries stay inside the
            # receiver's ±300s replay window — which is why the whole
            # request is built here rather than once outside the loop.
            timestamp = str(int(time.time()))
            headers["X-Webhook-Timestamp"] = timestamp
            if secret:
                headers["X-Webhook-Signature-V2"] = sign_v2(secret, timestamp, body)
                headers["X-Webhook-Signature"] = sign_plain(secret, body)
            else:
                logger.warning("webhook_unsigned_no_secret", webhook_event=event)
            response = await self._get_client().post(url, content=body, headers=headers)
            if response.status_code < 300:
                logger.info("webhook_delivered", webhook_event=event, attempt=number, status=response.status_code)
                return True
            logger.warning(
                "webhook_attempt_rejected",
                webhook_event=event,
                attempt=number,
                status=response.status_code,
                detail=response.text[:200],
            )
            # Deliberately NOT classify_response: this path retried every
            # non-2xx before the shared helper existed, including 4xx, and
            # the receiver contract is consumer-defined. Only the newer
            # Telegram / GitHub-comment channels drop permanent 4xx.
            raise TransientSendError(f"http_{response.status_code}", retry_after=advertised_delay(response))

        try:
            return await send_with_retry(
                attempt,
                channel="webhook",
                max_attempts=self._max_attempts,
                backoff_seconds=self._backoff_seconds,
                sleep=self._sleep,
            )
        except (SendError, httpx.HTTPError):
            logger.error("webhook_delivery_failed", webhook_event=event, attempts=self._max_attempts)
            return False

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        """Cancel in-flight deliveries and close the owned HTTP client."""
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._owns_client and self._client is not None:
            await self._client.aclose()
