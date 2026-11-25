"""Asks go to people (#179): an owner is asked as their person, on their repository's forge, in their own hours.

- An owner of a GitLab organization's repository is its GitLab login; a person is asked by their email, else as
  ``<forge>:<login>``; a disabled person is not asked, and a decision only disabled people own goes to the group.
- A person's ``timezone``, ``hours`` and ``weekdays`` win over their organization's, field by field (D3), and the
  rest of the organization's values (the reminder, the expiry, the limit) stay its own. A group follows the
  organization.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from embry0.services.ask_loop import AskLoop
from embry0.services.asks import AskPolicy, recipients, release
from tests.fixtures.asks import asks_read
from tests.fixtures.people_directory import asked as people_reader
from tests.fixtures.people_directory import person

try:
    from embry0.services.owner_people import Directory
except ImportError:  # pragma: no cover - the message says what is missing
    import pytest

    pytest.fail("asks resolve to no people: there is no embry0.services.owner_people", pytrace=False)

# Tuesday 2026-10-27, 15:00 UTC: 10:00 in Chicago, 00:00 on Wednesday in Tokyo.
TUE_15_UTC = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
CHICAGO = {"enabled": True, "timezone": "America/Chicago", "hours": "09-17", "weekdays": "mon-fri"}
GL = "acme/web"  # a GitLab organization's repository
GH = "globex/gizmos"

ADA = person("Ada", email="ada@example.com", identities=[("gitlab", "ada")])
NIGHT = person("Night", email="night@example.com", identities=[("github", "night")], timezone="Asia/Tokyo")
EARLY = person("Early", email="early@example.com", identities=[("github", "early")], hours="06-08")
WEEKEND = person("Weekend", email="weekend@example.com", identities=[("github", "weekend")], weekdays="sat-sun")
GONE = person("Gone", email="gone@example.com", identities=[("github", "gone")], disabled=True)
PEOPLE = Directory(people=(ADA, NIGHT, EARLY, WEEKEND, GONE), forges={GL: "gitlab", GH: "github"})


def _decision(decision_id: str, repo: str, *owners: str) -> dict[str, Any]:
    return {"id": decision_id, "repo": repo, "owners": list(owners)}


def test_an_owner_is_asked_as_their_person_on_their_repository_s_forge() -> None:
    assert recipients(_decision("d1", GL, "Ada", "bo"), PEOPLE.recipient) == ["ada@example.com", "gitlab:bo"], (
        "a GitLab organization's owner is their GitLab login: Ada by email, bo, whom no person holds, as gitlab:bo"
    )
    assert recipients(_decision("d2", GH, "ada"), PEOPLE.recipient) == ["github:ada"], (
        "Ada's GitLab login is not a GitHub one"
    )
    assert recipients(_decision("d3", GH, "gone", "night"), PEOPLE.recipient) == ["night@example.com"], (
        "a disabled person is not asked"
    )
    assert recipients(_decision("d4", GH, "gone"), PEOPLE.recipient, unowned_group="triage") == ["group:triage"], (
        "a decision only disabled people own is nobody's: it goes to the group"
    )


def _policy(*decisions: dict[str, Any]) -> AskPolicy:
    return AskPolicy(asks_read(CHICAGO), decisions, PEOPLE)


def test_a_person_s_own_hours_win_field_by_field_and_the_rest_stay_the_organization_s() -> None:
    policy = _policy(_decision("d1", GH, "night"))
    org = policy.settings("d1")
    night = policy.settings("d1", "night@example.com")
    assert (night.timezone, night.hours, night.weekdays) == ("Asia/Tokyo", org.hours, org.weekdays), (
        "Night set a time zone and nothing else: their hours and weekdays are the organization's"
    )
    early = policy.settings("d1", "early@example.com")
    assert (early.timezone, early.hours) == (org.timezone, (6, 8))
    assert policy.settings("d1", "weekend@example.com").weekdays == frozenset({5, 6})
    for who in ("night@example.com", "early@example.com", "weekend@example.com"):
        s = policy.settings("d1", who)
        assert (s.remind_hours, s.expire_days, s.max_open_per_person) == (
            org.remind_hours,
            org.expire_days,
            org.max_open_per_person,
        ), f"{who}: the reminder, the expiry and the limit stay the organization's"
    assert policy.settings("d1", "group:triage") == org, "a group follows the organization"
    assert policy.settings("d1", "github:nobody") == org, "a login no person holds follows the organization"


def _queued(ask_id: str, decision_id: str, recipient: str) -> dict[str, Any]:
    return {
        "id": ask_id,
        "decision_id": decision_id,
        "recipient": recipient,
        "state": "queued",
        "due_at": TUE_15_UTC - timedelta(minutes=1),
        "created_at": TUE_15_UTC - timedelta(hours=1),
        "reminders": 0,
    }


def test_an_ask_goes_out_in_its_person_s_working_time() -> None:
    """At 10:00 in Chicago, Chicago's working time: an ask to the organization's people goes out; one to Night,
    whose day is in Tokyo (midnight there), and one to Early, who works 06 to 08, wait."""
    decisions = [_decision(f"d{i}", GH, who) for i, who in enumerate(("night", "early", "weekend"))]
    decisions.append(_decision("d9", GH, "plain"))
    live = [
        _queued("night", "d0", "night@example.com"),
        _queued("early", "d1", "early@example.com"),
        _queued("weekend", "d2", "weekend@example.com"),
        _queued("plain", "d9", "github:plain"),
    ]
    out = release(live, {d["id"]: i for i, d in enumerate(decisions)}, TUE_15_UTC, _policy(*decisions))
    assert [a["id"] for a, _ in out] == ["plain"], (
        f"only the ask in its person's working time goes out: {[a['id'] for a, _ in out]}"
    )


class _Asks:
    def __init__(self) -> None:
        self.queued: list[tuple[str, str, datetime]] = []

    async def live(self) -> list[dict[str, Any]]:
        return []

    async def expired_pairs(self) -> dict[tuple[str, str], datetime | None]:
        return {}

    async def queue(self, asks: Any) -> list[str]:
        self.queued.extend(asks)
        return [f"ask-{i}" for i, _ in enumerate(self.queued)]

    async def close(self, ask_ids: Any, *, state: str, now: datetime) -> int:
        return 0


async def test_the_loop_queues_each_ask_for_its_person_s_next_working_time() -> None:
    asks = _Asks()
    decisions = [_decision("d1", GH, "night", "plain")]

    async def listed() -> list[dict[str, Any]]:
        return decisions

    async def section() -> Any:
        return asks_read(CHICAGO)

    loop = AskLoop(
        settings=section, asks=asks, decisions=listed, people=people_reader(PEOPLE), clock=lambda: TUE_15_UTC
    )
    await loop.tick()
    due = {recipient: at for _decision_id, recipient, at in asks.queued}
    assert due["github:plain"] == TUE_15_UTC, "Chicago's working time: due now"
    assert due["night@example.com"] == datetime(2026, 10, 28, 0, 0, tzinfo=UTC), (
        f"Night's next working hour is 09:00 Wednesday in Tokyo: {due['night@example.com']}"
    )
