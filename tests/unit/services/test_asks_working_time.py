"""A queued ask goes out only within its organization's working time (#276).

An ask held back by ``max_open_per_person`` waits queued, long past its due
time. When a slot frees, it is released only within the working hours and
weekdays of its decision's organization, read in that organization's time
zone, the check a reminder gets; otherwise it waits for the next working
time. Each case below has the slot free and the ask due three days ago, so
working time alone decides.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.services.asks import AskPolicy, next_working_time, release
from tests.fixtures.asks import asks_read

#: The deployment's own hours, which no case's organization uses.
DEPLOYMENT = {"enabled": True, "hours": "09-18", "weekdays": "mon-fri", "timezone": "UTC"}


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


#: (organization's layer, now, when its queued ask goes out: now when it is working time).
CASES = {
    "tokyo-friday-17:00": ({"timezone": "Asia/Tokyo"}, _utc(2026, 10, 30, 8, 0), _utc(2026, 10, 30, 8, 0)),
    "tokyo-friday-18:00-closed": ({"timezone": "Asia/Tokyo"}, _utc(2026, 10, 30, 9, 0), _utc(2026, 11, 2, 0, 0)),
    # Friday in UTC, already Saturday in Tokyo.
    "tokyo-saturday-02:00": ({"timezone": "Asia/Tokyo"}, _utc(2026, 10, 30, 17, 0), _utc(2026, 11, 2, 0, 0)),
    # Saturday in UTC, still Friday afternoon in Los Angeles.
    "los-angeles-friday-17:30": (
        {"timezone": "America/Los_Angeles"},
        _utc(2026, 10, 31, 0, 30),
        _utc(2026, 10, 31, 0, 30),
    ),
    # Monday in UTC, still Sunday night in Los Angeles; Monday 09:00 there is PST, after the clocks went back.
    "los-angeles-sunday-22:00": (
        {"timezone": "America/Los_Angeles"},
        _utc(2026, 11, 2, 6, 0),
        _utc(2026, 11, 2, 17, 0),
    ),
    # Chicago left daylight time on Sunday 1 November: 14:30 UTC was 09:30 on Friday, 08:30 on Monday.
    "chicago-friday-09:30-cdt": (
        {"timezone": "America/Chicago"},
        _utc(2026, 10, 30, 14, 30),
        _utc(2026, 10, 30, 14, 30),
    ),
    "chicago-monday-08:30-cst": ({"timezone": "America/Chicago"}, _utc(2026, 11, 2, 14, 30), _utc(2026, 11, 2, 15, 0)),
    # Berlin entered summer time on Sunday 28 March 2027: 07:30 UTC was 08:30 on Friday, 09:30 on Monday.
    "berlin-friday-08:30-cet": ({"timezone": "Europe/Berlin"}, _utc(2027, 3, 26, 7, 30), _utc(2027, 3, 26, 8, 0)),
    "berlin-monday-09:30-cest": ({"timezone": "Europe/Berlin"}, _utc(2027, 3, 29, 7, 30), _utc(2027, 3, 29, 7, 30)),
    # A Sunday-to-Thursday week: Friday is off, Sunday is not.
    "dubai-friday-off": (
        {"timezone": "Asia/Dubai", "hours": "08-17", "weekdays": "sun-thu"},
        _utc(2026, 10, 30, 6, 0),
        _utc(2026, 11, 1, 4, 0),
    ),
    # Hours that cross the hour the clocks go back (01:00-02:00 happens twice) and the hour they skip.
    "chicago-second-01:30-on-fall-back": (
        {"timezone": "America/Chicago", "hours": "01-03", "weekdays": "mon-sun"},
        _utc(2026, 11, 1, 7, 30),
        _utc(2026, 11, 1, 7, 30),
    ),
    "chicago-01:59-before-the-skipped-hour": (
        {"timezone": "America/Chicago", "hours": "02-04", "weekdays": "mon-sun"},
        _utc(2027, 3, 14, 7, 59),
        _utc(2027, 3, 14, 8, 0),
    ),
}


def _queued(decision_id: str, now: datetime) -> dict[str, Any]:
    return {
        "id": f"ask-{decision_id}",
        "decision_id": decision_id,
        "recipient": "ada@example.com",
        "state": "queued",
        "due_at": now - timedelta(days=3),
        "created_at": now - timedelta(days=3),
        "reminders": 0,
    }


@pytest.mark.parametrize(("layer", "now", "goes_at"), CASES.values(), ids=CASES.keys())
def test_a_queued_ask_goes_out_only_within_its_organization_s_working_time(
    layer: dict[str, Any], now: datetime, goes_at: datetime
) -> None:
    policy = AskPolicy(asks_read(DEPLOYMENT, {"acme": layer}), [{"id": "d1", "repo": "acme/widgets"}])
    ask = _queued("d1", now)
    assert next_working_time(now, policy.settings("d1")) == goes_at, "the case's own arithmetic"

    released = [a["id"] for a, _ in release([ask], {}, now, policy)]
    if goes_at == now:
        assert released == ["ask-d1"], f"within acme's working time at {now:%a %H:%M} UTC, the ask goes: {released}"
        return
    assert released == [], (
        f"at {now:%a %Y-%m-%d %H:%M} UTC it is outside acme's working time ({layer}): the ask waits, "
        f"although it was due three days ago and its person holds nothing: {released}"
    )
    assert release([ask], {}, goes_at - timedelta(minutes=1), policy) == [], "and still waits a minute before"
    assert [a["id"] for a, _ in release([ask], {}, goes_at, policy)] == ["ask-d1"], (
        f"it goes at acme's next working time, {goes_at:%a %Y-%m-%d %H:%M} UTC"
    )


def test_each_queued_ask_waits_for_its_own_organization_s_working_time() -> None:
    """Friday 18:30 UTC: Saturday 03:30 in Tokyo, Friday 13:30 in Chicago, after the deployment's 09-18 UTC."""
    now = _utc(2026, 10, 30, 18, 30)
    section = asks_read(DEPLOYMENT, {"acme": {"timezone": "Asia/Tokyo"}, "globex": {"timezone": "America/Chicago"}})
    decisions = [
        {"id": "acme", "repo": "acme/widgets"},
        {"id": "globex", "repo": "globex/gizmos"},
        {"id": "none", "repo": ""},
    ]
    live = [_queued(d["id"], now) for d in decisions]
    released = [a["id"] for a, _ in release(live, {}, now, AskPolicy(section, decisions))]
    assert released == ["ask-globex"], (
        "only globex is at work: acme's Tokyo is asleep on Saturday, and an ask about a decision with no "
        f"repository keeps the deployment's 09-18 UTC: {released}"
    )


def test_a_slot_freed_at_night_releases_the_next_ask_in_the_morning() -> None:
    """The case of #276: an answer at night frees a slot; the ask behind it waits for the working day."""
    section = asks_read(DEPLOYMENT, {"acme": {"timezone": "Asia/Tokyo", "max_open_per_person": 1}})
    decisions = [{"id": "d1", "repo": "acme/widgets"}, {"id": "d2", "repo": "acme/widgets"}]
    policy = AskPolicy(section, decisions)
    friday_16_jst = _utc(2026, 10, 30, 7, 0)
    held = {**_queued("d1", friday_16_jst), "state": "sent", "sent_at": friday_16_jst}
    behind = _queued("d2", friday_16_jst)
    assert release([held, behind], {}, friday_16_jst, policy) == [], "ada holds acme's one ask: d2 waits"

    saturday_02_jst = _utc(2026, 10, 30, 17, 0)
    assert release([behind], {}, saturday_02_jst, policy) == [], (
        "ada answered d1 at 02:00 on Saturday in Tokyo: the slot is free, but d2 waits for acme's working time"
    )
    monday_09_jst = _utc(2026, 11, 2, 0, 0)
    assert [a["id"] for a, _ in release([behind], {}, monday_09_jst, policy)] == ["ask-d2"], (
        "d2 goes at 09:00 on Monday in Tokyo"
    )
