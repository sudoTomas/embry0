"""An ask waiting unsent, behind its person's limit or for working hours, does not expire (#295).

ada works 09-18, Monday to Friday, in UTC, and may hold one unanswered ask.
Two decisions wait on her from a Friday evening: the first ask goes out on
Monday, the second waits behind it. She answers the first the next Friday at
19:00, after hours. Before #295 the second ask, queued a week before at
20:00, expired at the tick at 20:02, never sent, and she was never asked
about that decision again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from embry0.services.ask_loop import AskLoop
from embry0.services.asks import AskPolicy, release, still_kept_from
from tests.fixtures.asks import asks_read

SECTION = asks_read(
    {
        "enabled": True,
        "hours": "09-18",
        "weekdays": "mon-fri",
        "timezone": "UTC",
        "max_open_per_person": 1,
        "expire_days": 7,
    }
)
#: Friday 23 October 2026, 20:00 UTC: after hours.
QUEUED = datetime(2026, 10, 23, 20, 0, tzinfo=UTC)
MONDAY_09 = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
#: The next Friday: ada answers the first ask at 19:00, and the loop ticks at 20:02, a week and two minutes on.
ANSWERED = datetime(2026, 10, 30, 19, 0, tzinfo=UTC)
TICK = datetime(2026, 10, 30, 20, 2, tzinfo=UTC)
NEXT_MONDAY_09 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class Asks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def live(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows if r["state"] in ("queued", "sent", "reminded")]

    async def expired_pairs(self) -> dict[tuple[str, str], Any]:
        return {
            (r["decision_id"], r["recipient"]): None for r in self.rows if r["state"] == "expired" and r.get("sent_at")
        }

    async def code(self, decision_id: str) -> str:
        return "ABCD"

    async def add(self, *, decision_id: str, recipient: str, due_at: datetime) -> str | None:
        ask_id = f"a-{decision_id}"
        self.rows.append(
            {"id": ask_id, "decision_id": decision_id, "recipient": recipient, "state": "queued", "due_at": due_at}
            | {"created_at": clock[0], "reminders": 0}
        )
        return ask_id

    async def queue(self, new: Any) -> list[str]:
        """Each ``(decision, person, due)`` as one ``add``: for an ask loop that queues a tick's asks at once."""
        return [ask_id for d, r, due in new if (ask_id := await self.add(decision_id=d, recipient=r, due_at=due))]

    async def close(self, ask_ids: Any, *, state: str, now: datetime) -> int:
        for r in self.rows:
            if r["id"] in set(ask_ids):
                r["state"] = state
        return len(set(ask_ids))

    def of(self, decision_id: str) -> dict[str, Any]:
        return next(r for r in self.rows if r["decision_id"] == decision_id)


clock = [QUEUED]


async def test_an_ask_held_from_friday_evening_to_the_next_monday_is_still_asked() -> None:
    clock[0] = QUEUED
    asks = Asks()
    decisions = [
        {"id": "d1", "repo": "acme/web", "owners": ["ada"]},
        {"id": "d2", "repo": "acme/web", "owners": ["ada"]},
    ]

    async def listed() -> list[dict[str, Any]]:
        return list(decisions)

    async def people() -> dict[str, str]:
        return {"ada": "ada@example.com"}

    async def section() -> Any:
        return SECTION

    loop = AskLoop(settings=section, asks=asks, decisions=listed, people=people, clock=lambda: clock[0])
    assert (await loop.tick())["queued"] == 2, "both decisions are queued on Friday evening, due on Monday at 09:00"

    policy = AskPolicy(SECTION, decisions)
    first = [a["id"] for a, _ in release(await asks.live(), {"d1": 0, "d2": 1}, MONDAY_09, policy)]
    assert first == ["a-d1"], f"on Monday ada gets the first ask, and the second waits behind her limit: {first}"
    asks.of("d1").update(state="sent", sent_at=MONDAY_09)

    asks.of("d1")["state"] = "answered"
    decisions.pop(0)
    clock[0] = TICK
    week_on = await loop.tick()
    assert asks.of("d2")["state"] == "queued", (
        f"ada answered the first ask on Friday at 19:00; at 20:02 the second, queued a week before and never sent, "
        f"still waits: it does not expire unsent: {week_on}"
    )
    assert week_on == {"expired": 0, "withdrawn": 0, "queued": 0}, week_on

    clock[0] = NEXT_MONDAY_09
    assert await loop.tick() == {"expired": 0, "withdrawn": 0, "queued": 0}, "nor over the weekend"
    handed = [a["id"] for a, _ in release(await asks.live(), {"d2": 0}, NEXT_MONDAY_09, policy)]
    assert handed == ["a-d2"], f"on Monday at 09:00 it goes out: {handed}"

    asks.of("d2").update(state="sent", sent_at=NEXT_MONDAY_09)
    clock[0] = NEXT_MONDAY_09 + timedelta(days=7)
    assert (await loop.tick())["expired"] == 1, "once sent, it expires a week after it was sent"


def test_a_queued_ask_never_handed_out_goes_before_one_handed_out_and_unreported() -> None:
    """Whatever their ranks: the one handed out before had its turn (#295)."""
    section = asks_read({"enabled": True, "hours": "00-24", "weekdays": "mon-sun", "max_open_per_person": 1})
    decisions = [{"id": "worse", "repo": "acme/web"}, {"id": "better", "repo": "acme/web"}]
    policy = AskPolicy(section, decisions)
    ask = {"recipient": "ada@example.com", "state": "queued", "due_at": QUEUED, "created_at": QUEUED, "reminders": 0}
    handed = {**ask, "id": "a-worse", "decision_id": "worse", "handed_out_at": QUEUED}
    fresh = {**ask, "id": "a-better", "decision_id": "better"}
    released = [a["id"] for a, _ in release([handed, fresh], {"worse": 0, "better": 1}, MONDAY_09, policy)]
    assert released == ["a-better"], (
        f"ada may hold one: the ask never handed out goes, though the other's decision ranks first: {released}"
    )
    assert [a["id"] for a, _ in release([handed], {"worse": 0}, MONDAY_09, policy)] == ["a-worse"], (
        "alone, the one handed out before goes again"
    )


def test_the_wait_before_asking_again_is_the_organization_s_expire_days() -> None:
    """acme's asks expire after 2 days and the deployment's after 7: an undelivered acme ask that expired three
    days ago may be asked again; one about a decision with no repository waits the deployment's seven."""
    section = asks_read({"enabled": True, "expire_days": 7}, {"acme": {"expire_days": 2}})
    policy = AskPolicy(section, [{"id": "acme", "repo": "acme/web"}, {"id": "none", "repo": ""}])
    since = MONDAY_09 - timedelta(days=3)
    expired_pairs = {("acme", "ada@example.com"): since, ("none", "ada@example.com"): since}
    assert still_kept_from(expired_pairs, MONDAY_09, policy) == {("none", "ada@example.com")}, (
        "acme's two-day wait is over; the deployment's seven-day one is not"
    )
