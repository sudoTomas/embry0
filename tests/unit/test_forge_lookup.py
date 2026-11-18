"""The window a lookup reads after a forge write whose outcome is unknown (#620)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from embry0.forge_lookup import LOOKUP_MARGIN, answered_at, forge_time, made_since

STARTED = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


def test_the_window_starts_a_margin_before_the_forges_answer_when_there_was_one() -> None:
    """The forge's clock, ten minutes behind or ahead of this host's: the window follows it, not the local time."""
    behind = STARTED - timedelta(minutes=10)
    ahead = STARTED + timedelta(minutes=10)
    assert made_since(STARTED, behind) == behind - LOOKUP_MARGIN
    assert made_since(STARTED, ahead) == ahead - LOOKUP_MARGIN


def test_the_window_starts_a_margin_before_the_write_after_a_lost_answer() -> None:
    assert made_since(STARTED, None) == STARTED - LOOKUP_MARGIN


def test_a_responses_date_header_is_the_forges_clock() -> None:
    response = httpx.Response(502, headers={"Date": "Sun, 01 Mar 2026 11:50:07 GMT"})
    assert answered_at(response) == datetime(2026, 3, 1, 11, 50, 7, tzinfo=UTC)


@pytest.mark.parametrize("value", [None, "", "not a date", "Sun, 99 Mar 2026 11:50:07 GMT"])
def test_an_answer_without_a_readable_date_has_no_time(value: str | None) -> None:
    response = httpx.Response(502, headers={} if value is None else {"Date": value})
    assert answered_at(response) is None
    assert answered_at(None) is None


def test_a_date_without_a_zone_is_taken_as_utc() -> None:
    """RFC 5322's ``-0000`` says the zone is unknown; HTTP dates are always GMT."""
    response = httpx.Response(502, headers={"Date": "Sun, 01 Mar 2026 11:50:07 -0000"})
    assert answered_at(response) == datetime(2026, 3, 1, 11, 50, 7, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-03-01T11:50:07Z", datetime(2026, 3, 1, 11, 50, 7, tzinfo=UTC)),
        ("2026-03-01T06:50:07-05:00", datetime(2026, 3, 1, 11, 50, 7, tzinfo=UTC)),
        ("2026-03-01T11:50:07", None),
        ("yesterday", None),
        (None, None),
    ],
)
def test_a_forge_timestamp_is_aware_or_none(value: str | None, expected: datetime | None) -> None:
    """A timestamp without a zone is None, not a naive datetime the window's aware one cannot be compared with."""
    assert forge_time(value) == expected
