"""Asks (spec 2026-10-27): who is asked about what and when, with codes never reused, and the loop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.services.ask_loop import AskLoop
from embry0.services.asks import (
    AskSettings,
    ask_settings,
    code_for,
    expired,
    next_working_time,
    plan,
    recipients,
    release,
    reminder_due,
)

S = AskSettings(enabled=True, hours=(9, 18), weekdays=frozenset(range(5)), timezone="America/Chicago")
# Tuesday 2026-10-27, 10:00 in Chicago (CDT, UTC-5).
TUE_10 = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def test_a_code_is_four_letters_from_the_decision_and_longer_when_taken() -> None:
    code = code_for("offer:crack:1", set())
    assert len(code) == 4 and code.isalpha() and code.isupper()
    assert code_for("offer:crack:1", set()) == code, "the same decision always gets the same letters"
    longer = code_for("offer:crack:1", {code})
    assert len(longer) == 5 and longer.startswith(code)


def test_the_hand_off_s_person_alone_is_asked_while_it_is_open() -> None:
    d = {"id": "d", "owners": ["ada", "@Bo"], "assigned_to": {"email": "cy@example.com"}}
    assert recipients(d, {"ada": "ada@example.com"}) == ["cy@example.com"]


def test_owners_are_asked_by_email_where_known_and_by_login_otherwise_and_held_ones_by_nobody() -> None:
    d = {"id": "d", "owners": ["Ada", "@bo", "ada"], "assigned_to": None}
    assert recipients(d, {"ada": "ada@example.com"}) == ["ada@example.com", "github:bo"]
    assert recipients({**d, "held": True}, {}) == []


def test_the_plan_asks_whoever_is_new_and_withdraws_what_is_no_longer_wanted() -> None:
    decisions = [{"id": "d1", "owners": ["ada"]}, {"id": "d2", "owners": ["bo"]}]
    live = [
        {"id": "a1", "decision_id": "d1", "recipient": "ada@example.com"},
        {"id": "a9", "decision_id": "gone", "recipient": "ada@example.com"},
    ]
    p = plan(decisions, live, {"ada": "ada@example.com"})
    assert p.ask == [("d2", "github:bo")] and p.withdraw == ["a9"]


def test_an_ask_waits_for_working_hours_in_the_recipient_s_zone() -> None:
    assert next_working_time(TUE_10, S) == TUE_10
    evening = datetime(2026, 10, 27, 23, 30, tzinfo=UTC)  # 18:30 in Chicago
    assert next_working_time(evening, S) == datetime(2026, 10, 28, 14, 0, tzinfo=UTC)
    saturday = datetime(2026, 10, 31, 15, 0, tzinfo=UTC)
    assert next_working_time(saturday, S) == datetime(2026, 11, 2, 15, 0, tzinfo=UTC), "Monday 9:00 CST"


def test_one_reminder_after_a_day_and_expiry_after_a_week() -> None:
    sent = {
        "state": "sent",
        "reminders": 0,
        "sent_at": TUE_10 - timedelta(hours=25),
        "created_at": TUE_10 - timedelta(days=1),
    }
    assert reminder_due(sent, TUE_10, S)
    assert not reminder_due({**sent, "reminders": 1}, TUE_10, S), "one reminder only"
    assert not reminder_due({**sent, "sent_at": TUE_10 - timedelta(hours=2)}, TUE_10, S)
    assert expired({**sent, "created_at": TUE_10 - timedelta(days=8)}, TUE_10, S)


def test_settings_read_hours_days_and_zone_and_fall_back_when_wrong() -> None:
    s = ask_settings(
        SimpleNamespace(asks_enabled=True, ask_hours="08-17", ask_weekdays="sun-thu", ask_timezone="Asia/Dubai")
    )
    assert s.enabled and s.hours == (8, 17) and s.weekdays == frozenset({6, 0, 1, 2, 3}) and s.timezone == "Asia/Dubai"
    bad = ask_settings(SimpleNamespace(ask_hours="nine to five", ask_weekdays="never"))
    assert bad.hours == (9, 18) and bad.weekdays == frozenset(range(5)) and bad.enabled is False


class FakeAsks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.codes: dict[str, str] = {}
        self.gone: set[tuple[str, str]] = set()

    async def live(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows if r["state"] in ("queued", "sent", "reminded")]

    async def expired_pairs(self) -> set[tuple[str, str]]:
        return set(self.gone)

    async def code(self, decision_id: str) -> str:
        return self.codes.setdefault(decision_id, code_for(decision_id, set(self.codes.values())))

    async def add(self, *, decision_id: str, recipient: str, due_at: datetime) -> str | None:
        ask_id = f"a{len(self.rows) + 1}"
        self.rows.append(
            {
                "id": ask_id,
                "decision_id": decision_id,
                "recipient": recipient,
                "state": "queued",
                "due_at": due_at,
                "created_at": TUE_10,
            }
        )
        return ask_id

    async def close(self, ask_ids: Any, *, state: str, now: datetime) -> int:
        for r in self.rows:
            if r["id"] in set(ask_ids):
                r["state"] = state
        return len(set(ask_ids))


@pytest.mark.asyncio
async def test_a_tick_queues_withdraws_and_expires_and_never_re_asks_an_expired_person() -> None:
    asks = FakeAsks()
    decisions = [{"id": "d1", "owners": ["ada"]}]

    async def listed() -> list[dict[str, Any]]:
        return decisions

    async def people() -> dict[str, str]:
        return {"ada": "ada@example.com"}

    loop = AskLoop(settings=S, asks=asks, decisions=listed, people=people, clock=lambda: TUE_10)
    assert await loop.tick() == {"expired": 0, "withdrawn": 0, "queued": 1}
    assert asks.rows[0]["due_at"] == TUE_10 and "d1" in asks.codes
    assert (await loop.tick())["queued"] == 0, "one live ask per person and decision"

    decisions[0] = {"id": "d1", "owners": ["ada"], "assigned_to": {"email": "bo@example.com"}}
    assert await loop.tick() == {"expired": 0, "withdrawn": 1, "queued": 1}, "a hand-off moves the ask to its person"

    asks.rows[1]["created_at"] = TUE_10 - timedelta(days=8)
    asks.gone.add(("d1", "bo@example.com"))
    assert await loop.tick() == {"expired": 1, "withdrawn": 0, "queued": 0}, "expired once, not asked again"


def test_a_person_holds_at_most_three_unanswered_asks_and_the_worst_go_first() -> None:
    def a(i: int, state: str = "queued", who: str = "ada@example.com", **over: Any) -> dict[str, Any]:
        return {
            "id": f"a{i}",
            "decision_id": f"d{i}",
            "recipient": who,
            "state": state,
            "due_at": TUE_10,
            "created_at": TUE_10,
            "reminders": 0,
            **over,
        }

    live = [a(1, "sent"), a(2), a(3), a(4), a(5, who="bo@example.com"), a(6, due_at=TUE_10 + timedelta(hours=1))]
    order = {"d4": 0, "d2": 1, "d3": 2, "d5": 3}
    released = release(live, order, TUE_10, S, working=True)
    assert [(x["id"], r) for x, r in released] == [("a4", False), ("a2", False), ("a5", False)]
    reminder = a(7, "sent", sent_at=TUE_10 - timedelta(hours=30))
    assert release([reminder], {}, TUE_10, S, working=True) == [(reminder, True)]
    assert release([reminder], {}, TUE_10, S, working=False) == [], "a reminder waits for working hours"
