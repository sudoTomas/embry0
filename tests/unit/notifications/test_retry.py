"""Shared notification retry policy (#252) — classification, backoff, budget.

The helper never touches the network: an attempt is any async callable, and
sleeping goes through an injected function so a 429 that advertises two
seconds costs the suite nothing.
"""

import email.utils
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import structlog.testing

from embry0.notifications.retry import (
    PermanentSendError,
    TransientSendError,
    classify_response,
    send_with_retry,
)


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers or {}, request=httpx.Request("POST", "http://t.test"))


class _Sleeps:
    """Records every delay instead of waiting it out."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 599])
def test_classify_response_treats_429_and_5xx_as_transient(status):
    with pytest.raises(TransientSendError) as exc:
        classify_response(_response(status))
    assert exc.value.reason == f"http_{status}"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_classify_response_treats_other_4xx_as_permanent(status):
    with pytest.raises(PermanentSendError) as exc:
        classify_response(_response(status))
    assert exc.value.reason == f"http_{status}"


def test_classify_response_carries_retry_after():
    with pytest.raises(TransientSendError) as exc:
        classify_response(_response(429, {"Retry-After": "2"}))
    assert exc.value.retry_after == 2.0


def test_classify_response_caps_an_absurd_retry_after():
    """A confused server must not park a notification for an hour."""
    with pytest.raises(TransientSendError) as exc:
        classify_response(_response(429, {"Retry-After": "3600"}))
    assert exc.value.retry_after == 30.0


def test_classify_response_without_retry_after_leaves_backoff_to_the_caller():
    with pytest.raises(TransientSendError) as exc:
        classify_response(_response(503))
    assert exc.value.retry_after is None


async def test_send_with_retry_returns_the_first_success():
    calls = []

    async def attempt(number: int) -> str:
        calls.append(number)
        return "sent"

    assert await send_with_retry(attempt, channel="t", sleep=_Sleeps()) == "sent"
    assert calls == [1]


async def test_send_with_retry_passes_the_attempt_number():
    seen = []

    async def attempt(number: int) -> str:
        seen.append(number)
        if number < 3:
            raise TransientSendError("http_503")
        return "sent"

    assert await send_with_retry(attempt, channel="t", sleep=_Sleeps()) == "sent"
    assert seen == [1, 2, 3]


async def test_send_with_retry_backs_off_exponentially():
    sleeps = _Sleeps()

    async def attempt(number: int) -> None:
        raise TransientSendError("http_503")

    with pytest.raises(TransientSendError):
        await send_with_retry(attempt, channel="t", max_attempts=4, backoff_seconds=0.5, sleep=sleeps)
    assert sleeps.delays == [0.5, 1.0, 2.0]


async def test_send_with_retry_prefers_an_advertised_retry_after():
    sleeps = _Sleeps()

    async def attempt(number: int) -> str:
        if number == 1:
            raise TransientSendError("http_429", retry_after=2.0)
        return "sent"

    assert await send_with_retry(attempt, channel="t", backoff_seconds=10.0, sleep=sleeps) == "sent"
    assert sleeps.delays == [2.0]


async def test_send_with_retry_does_not_retry_permanent_failures():
    calls = []

    async def attempt(number: int) -> None:
        calls.append(number)
        raise PermanentSendError("http_400")

    with pytest.raises(PermanentSendError):
        await send_with_retry(attempt, channel="t", sleep=_Sleeps())
    assert calls == [1]


async def test_send_with_retry_treats_transport_errors_as_transient():
    calls = []

    async def attempt(number: int) -> str:
        calls.append(number)
        if number == 1:
            raise httpx.ConnectError("receiver down")
        return "sent"

    assert await send_with_retry(attempt, channel="t", sleep=_Sleeps()) == "sent"
    assert calls == [1, 2]


async def test_send_with_retry_reraises_the_last_failure_when_the_budget_runs_out():
    calls = []

    async def attempt(number: int) -> None:
        calls.append(number)
        raise TransientSendError("http_503")

    with pytest.raises(TransientSendError):
        await send_with_retry(attempt, channel="t", max_attempts=3, sleep=_Sleeps())
    assert calls == [1, 2, 3]


async def test_send_with_retry_lets_unrelated_exceptions_through_untouched():
    async def attempt(number: int) -> None:
        raise RuntimeError("bug in the attempt")

    with pytest.raises(RuntimeError):
        await send_with_retry(attempt, channel="t", sleep=_Sleeps())


def test_classify_response_retries_a_4xx_that_advertises_retry_after():
    """GitHub signals a secondary rate limit with 403 + ``Retry-After``."""
    with pytest.raises(TransientSendError) as exc:
        classify_response(_response(403, {"Retry-After": "7"}))
    assert exc.value.retry_after == 7.0


def test_classify_response_retries_a_4xx_with_no_rate_limit_left():
    """The other secondary-rate-limit shape: 403 + ``x-ratelimit-remaining: 0``."""
    with pytest.raises(TransientSendError) as exc:
        classify_response(_response(403, {"x-ratelimit-remaining": "0"}))
    assert exc.value.reason == "http_403"


def test_classify_response_keeps_a_4xx_with_quota_left_permanent():
    with pytest.raises(PermanentSendError):
        classify_response(_response(403, {"x-ratelimit-remaining": "42"}))


def test_classify_response_parses_an_http_date_retry_after():
    when = email.utils.format_datetime(datetime.now(UTC) + timedelta(seconds=5))
    with pytest.raises(TransientSendError) as exc:
        classify_response(_response(429, {"Retry-After": when}))
    assert exc.value.retry_after is not None
    assert 3.0 <= exc.value.retry_after <= 5.0


def test_classify_response_ignores_a_retry_after_already_in_the_past():
    when = email.utils.format_datetime(datetime.now(UTC) - timedelta(seconds=30))
    with pytest.raises(TransientSendError) as exc:
        classify_response(_response(429, {"Retry-After": when}))
    assert exc.value.retry_after is None


def test_classify_response_ignores_a_zero_retry_after():
    """``Retry-After: 0`` must not beat the backoff curve into a hot loop."""
    with pytest.raises(TransientSendError) as exc:
        classify_response(_response(429, {"Retry-After": "0"}))
    assert exc.value.retry_after is None


async def test_send_with_retry_caps_the_exponential_fallback():
    sleeps = _Sleeps()

    async def attempt(number: int) -> None:
        raise TransientSendError("http_503")

    with pytest.raises(TransientSendError):
        await send_with_retry(attempt, channel="t", max_attempts=4, backoff_seconds=20.0, sleep=sleeps)
    assert sleeps.delays == [20.0, 30.0, 30.0]


async def test_send_with_retry_logs_notification_failed_on_a_permanent_failure():
    """A dropped notification is observable even when nothing was retried."""

    async def attempt(number: int) -> None:
        raise PermanentSendError("http_400")

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(PermanentSendError):
            await send_with_retry(attempt, channel="telegram", sleep=_Sleeps())

    failures = [entry for entry in captured if entry["event"] == "notification_failed"]
    assert len(failures) == 1
    assert failures[0]["channel"] == "telegram"
    assert failures[0]["attempts"] == 1
    assert failures[0]["reason"] == "http_400"
    assert failures[0]["permanent"] is True


async def test_send_with_retry_logs_notification_failed_when_the_budget_runs_out():
    async def attempt(number: int) -> None:
        raise TransientSendError("http_503")

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(TransientSendError):
            await send_with_retry(attempt, channel="telegram", sleep=_Sleeps())

    failures = [entry for entry in captured if entry["event"] == "notification_failed"]
    assert len(failures) == 1
    assert failures[0]["attempts"] == 3
    assert failures[0]["permanent"] is False


# ---- #45: a send that must not be made twice ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "headers", "refused"),
    [(429, {}, True), (403, {"X-RateLimit-Remaining": "0"}, True), (500, {}, False), (503, {}, False)],
    ids=["429", "rate-limited-403", "500", "503"],
)
def test_a_transient_failure_says_whether_the_target_refused_it(status, headers, refused) -> None:
    with pytest.raises(TransientSendError) as raised:
        classify_response(_response(status, headers))
    assert raised.value.refused is refused


@pytest.mark.parametrize(
    ("failure", "attempts"),
    [
        (TransientSendError("http_429", refused=True), 3),
        (httpx.ConnectError("never connected"), 3),
        (httpx.ConnectTimeout("never connected"), 3),
        (TransientSendError("http_503"), 1),
        (httpx.ReadTimeout("the answer was lost"), 1),
        (httpx.RemoteProtocolError("the answer was dropped"), 1),
    ],
    ids=["429", "connect-error", "connect-timeout", "503", "read-timeout", "dropped-answer"],
)
async def test_a_send_that_is_not_repeatable_is_sent_again_only_when_it_was_not_acted_on(failure, attempts) -> None:
    calls: list[int] = []

    async def attempt(number: int) -> None:
        calls.append(number)
        raise failure

    async def _sleep(_delay: float) -> None:
        return None

    with pytest.raises(type(failure)):
        await send_with_retry(attempt, channel="test", sleep=_sleep, repeatable=False)
    assert len(calls) == attempts, f"{type(failure).__name__}: {len(calls)} attempts"
