"""Shared retry policy for outbound notification sends (#252).

Every notification channel faces the same question after a failed send: was
that a blip worth another attempt, or a rejection that will come back
identical next time? Answering it once keeps Telegram, the GitHub question
comment and the signed outbound webhook from drifting apart — and stops a
single 503 from leaving a job in ``awaiting_input`` with no visible question
on that channel.

An *attempt* is an async callable performing one complete send, request
building included, so a per-attempt signature or timestamp is re-derived
naturally. It reports its outcome by returning (success) or by raising
:class:`TransientSendError` / :class:`PermanentSendError`.
:func:`send_with_retry` owns the loop, the backoff and the
``notification_retry`` / ``notification_failed`` log lines; it sleeps through
an injected function, so tests never wait and need no network.

``Retry-After`` parsing is reused from :mod:`embry0.execution.forge_client`
instead of written a second time. Only the classification differs: a
notification target is not a forge, so every 5xx counts as transient here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import NoReturn

import httpx
import structlog

from embry0.execution.forge_client import MAX_BACKOFF_SECONDS, NOT_SENT_ERRORS, rate_limited, retry_after_seconds

logger = structlog.get_logger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 1.0


class SendError(Exception):
    """A send attempt failed. ``reason`` is what shows up in the log line."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TransientSendError(SendError):
    """Worth another attempt — a 429, a 5xx, or a dropped connection.

    ``refused``: the target refused the send for its rate (a 429, or a 4xx
    carrying a rate-limit signal), so it did not act on it; after a 5xx that
    is unknown (#45).
    """

    def __init__(self, reason: str, *, retry_after: float | None = None, refused: bool = False) -> None:
        super().__init__(reason)
        self.retry_after = retry_after
        self.refused = refused


class PermanentSendError(SendError):
    """Not worth another attempt — a 4xx the next request would repeat."""


def advertised_delay(response: httpx.Response) -> float | None:
    """The delay the server asked for, capped at :data:`MAX_BACKOFF_SECONDS`.

    Header parsing is the forge client's; the cap and the floor are this
    layer's, so one rate limit cannot park a notification for an hour and a
    ``Retry-After: 0`` cannot undercut the backoff curve into a hot loop —
    zero means "no delay advertised", not "retry immediately".
    """
    advertised = retry_after_seconds(response)
    if advertised is None or advertised <= 0:
        return None
    return min(advertised, MAX_BACKOFF_SECONDS)


def classify_response(response: httpx.Response) -> NoReturn:
    """Raise the send error matching a failed response.

    ``429``, every ``5xx``, and any ``4xx`` carrying a rate-limit signal
    (GitHub's secondary limits come back as ``403``) are transient, carrying
    the advertised ``Retry-After`` capped at :data:`MAX_BACKOFF_SECONDS` so
    one rate limit cannot park a notification for an hour. Any other status
    is permanent: a rejected token or a malformed payload fails identically
    next time.

    Call this only on a response already judged a failure — it always raises.
    """
    status = response.status_code
    refused = status == 429 or (400 <= status < 500 and rate_limited(response))
    if refused or status >= 500:
        raise TransientSendError(f"http_{status}", retry_after=advertised_delay(response), refused=refused)
    raise PermanentSendError(f"http_{status}")


def not_acted_on(exc: BaseException) -> bool:
    """Whether a failed send proves the target did not act on it (#45): a rate-limit refusal, or a connection that
    never opened. After any other failure (a 5xx, an answer lost in transit) the outcome is unknown."""
    return (isinstance(exc, TransientSendError) and exc.refused) or isinstance(exc, NOT_SENT_ERRORS)


async def send_with_retry[T](
    attempt: Callable[[int], Awaitable[T]],
    *,
    channel: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    repeatable: bool = True,
) -> T:
    """Call ``attempt`` until it succeeds or the transient budget is spent.

    ``attempt`` receives the 1-based attempt number (channels log it) and
    returns the send's result. :class:`TransientSendError` and any
    ``httpx.HTTPError`` — what a reset connection or a read timeout surfaces
    as — buy another try; :class:`PermanentSendError` and every other
    exception propagate immediately. The final failure is re-raised so each
    channel decides for itself whether to swallow or surface it.

    Every lost notification logs exactly one ``notification_failed``,
    whether the budget ran out or the first attempt was rejected outright
    (``permanent=True``) — an alert keyed on that event must not miss the
    drops that never got as far as a retry. Delays are capped at
    :data:`MAX_BACKOFF_SECONDS`, advertised and computed alike.

    ``repeatable=False`` is for a send that must not be made twice (#45), such
    as a GitHub comment: one the target made but answered 5xx, or whose
    answer was lost, would be posted again. It is sent again only when the
    target did not act on it: a rate-limit refusal, or a connection that never
    opened (``NOT_SENT_ERRORS``).
    """
    number = 0
    while True:
        number += 1
        try:
            return await attempt(number)
        except PermanentSendError as exc:
            logger.error(
                "notification_failed",
                channel=channel,
                attempts=number,
                reason=exc.reason,
                permanent=True,
            )
            raise
        except (TransientSendError, httpx.HTTPError) as exc:
            reason = exc.reason if isinstance(exc, SendError) else type(exc).__name__
            if number >= max_attempts or not (repeatable or not_acted_on(exc)):
                logger.error(
                    "notification_failed",
                    channel=channel,
                    attempts=number,
                    reason=reason,
                    permanent=False,
                )
                raise
            advertised = exc.retry_after if isinstance(exc, TransientSendError) else None
            delay = advertised if advertised is not None else backoff_seconds * (2 ** (number - 1))
            delay = min(delay, MAX_BACKOFF_SECONDS)
            logger.warning(
                "notification_retry",
                channel=channel,
                attempt=number,
                reason=reason,
                delay_seconds=round(delay, 2),
            )
            await sleep(delay)
