"""The asks API (spec 2026-10-27): the feed a chat sends from, and a button press answered once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import asks as asks_api
from embry0.api.v1 import decisions as decisions_api
from embry0.api.v1 import proposals as proposals_api
from embry0.config import Embry0Config
from embry0.settings.document import read_sections
from embry0.settings.sections import CATALOG
from embry0.settings.sections.asks import DAYS
from embry0.settings.store import Snapshot
from tests.fixtures.asks import asks_snapshot

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
NOW = datetime.now(UTC)
#: Working hours that hold whenever the test runs: NOW is the real time.
ALWAYS = {"hours": "00-24", "weekdays": "mon-sun"}


class FakeAsks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.codes = {"offer:crack:1": "ABCD", "done:job-1": "WXYZ"}
        #: Each decision's repository as the table would read it back from the row the decision comes from.
        self.repos: dict[str, str] = {}

    async def live(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows if r["state"] in ("queued", "sent", "reminded")]

    async def code(self, decision_id: str) -> str:
        return self.codes[decision_id]

    async def lease_out(self, decide: Any, *, now: datetime, until: datetime) -> dict[str, datetime | None]:
        """As the table leases them (#293): what ``decide`` picks from the live asks, each still what was picked
        and with no lease running, a queued one stamped the first time it is handed out (#295)."""
        picked = dict(decide(await self.live()))
        leased: dict[str, datetime | None] = {}
        for r in self.rows:
            if r["id"] not in picked or (r.get("lease_until") and r["lease_until"] > now):
                continue
            if r["state"] == ("sent" if picked[r["id"]] else "queued"):
                if r["state"] == "queued":
                    r["handed_out_at"] = r.get("handed_out_at") or now
                r["lease_until"] = until
                leased[r["id"]] = r.get("handed_out_at")
        return leased

    async def decision_of(self, code: str) -> str | None:
        return next((d for d, c in self.codes.items() if c == code), None)

    async def close(self, ids: Any, *, state: str, now: datetime) -> int:
        for r in self.rows:
            if r["id"] in set(ids):
                r["state"] = state
        return 1

    async def sent(
        self, ask_id: str, *, message_ref: str, now: datetime, duplicate_within: timedelta
    ) -> dict[str, Any] | None:
        """As the table does: a second report within ``duplicate_within`` of the first, with no reminder handed out
        since, changes nothing but the lease (#293)."""
        for r in self.rows:
            if r["id"] == ask_id and r["state"] in ("queued", "sent"):
                first = r.get("sent_at")
                again = (
                    r["state"] == "sent"
                    and not r.get("lease_until")
                    and isinstance(first, datetime)
                    and now - first < duplicate_within
                )
                if not again:
                    r["reminders"] += 0 if r["state"] == "queued" else 1
                    r["state"] = "sent" if r["state"] == "queued" else "reminded"
                r["message_ref"] = message_ref or r.get("message_ref")
                r.setdefault("sent_at", now)
                r["lease_until"] = None
                return dict(r)
        return None

    async def answered(self, decision_id: str, *, answer: str, by: str, now: datetime) -> list[dict[str, Any]]:
        out = []
        for r in self.rows:
            if r["decision_id"] == decision_id and r["state"] in ("queued", "sent", "reminded"):
                r.update(state="answered", answer=answer, answered_by=by, answered_at=now)
                out.append(dict(r))
        return out

    async def last_answer(self, decision_id: str) -> dict[str, Any] | None:
        done = [r for r in self.rows if r["decision_id"] == decision_id and r["state"] == "answered"]
        return dict(done[-1]) if done else None

    async def recent(self, *, limit: int = 100, repos: Any = None, decision_ids: Any = ()) -> list[dict[str, Any]]:
        """As the table narrows them: by the decision's repository, read back from ``self.repos``, or by id."""
        rows = [dict(r) for r in self.rows]
        if repos is not None:
            named = set(decision_ids)
            rows = [
                {**r, "decision_repo": self.repos.get(r["decision_id"])}
                for r in rows
                if r["decision_id"] in named or str(self.repos.get(r["decision_id"], "")).lower() in set(repos)
            ]
        return rows[:limit]

    async def decision_repos(self, decision_ids: Any) -> dict[str, str]:
        """Each decision's repository as the table reads it back: ``self.repos``, set by a test."""
        return {d: self.repos[d] for d in decision_ids if d in self.repos}

    async def get(self, ask_id: str) -> dict[str, Any] | None:
        return next((dict(r) for r in self.rows if r["id"] == ask_id), None)


def ask(ask_id: str, decision_id: str, recipient: str, **over: Any) -> dict[str, Any]:
    return {
        "id": ask_id,
        "decision_id": decision_id,
        "recipient": recipient,
        "state": "queued",
        "reminders": 0,
        "due_at": NOW - timedelta(minutes=1),
        "created_at": NOW - timedelta(minutes=5),
        "message_ref": None,
        **over,
    }


CRACK = {
    "id": "offer:crack:1",
    "stage": "offered",
    "title": "A crack",
    "repo": "acme/web",
    "owners": ["ada"],
    "actions": ["start", "decline", "later", "send_to"],
    "links": {"proposal": "crack:1"},
    "assigned_to": None,
}
JOB = {
    "id": "done:job-1",
    "stage": "done",
    "title": "A job",
    "repo": "acme/web",
    "owners": ["ada"],
    "actions": ["merge", "send_back", "reject", "run_again", "send_to"],
    "links": {"job": "job-1"},
    "assigned_to": None,
}


class FakeUsers:
    async def get_by_email(self, email: str) -> Any:
        people = {
            "ada@example.com": SimpleNamespace(
                email="ada@example.com", name="Ada", github_login="ada", tier="operator"
            ),
            "cy@example.com": SimpleNamespace(email="cy@example.com", name="Cy", github_login="cy", tier="operator"),
            "root@example.com": SimpleNamespace(email="root@example.com", name="Root", github_login=None, tier="admin"),
        }
        return people.get(email.lower())


class FakeSettings:
    """The settings store as the feed reads it: a snapshot of a document held in memory."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.reads = 0

    async def snapshot(self) -> Snapshot:
        self.reads += 1
        return Snapshot(generation=self.reads, data=self.data, sections=read_sections(CATALOG, self.data))


class LayeredSettings:
    """The settings store as the feed reads it, with organization layers: owner to its ``asks`` values."""

    def __init__(self, deployment: dict[str, Any], organizations: dict[str, dict[str, Any]]) -> None:
        self.snapshots = asks_snapshot(deployment, organizations)

    async def snapshot(self) -> Snapshot:
        return self.snapshots


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def emit_soon(self, event: str, data: dict[str, Any]) -> None:
        self.sent.append((event, data))


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    app = create_app(
        Embry0Config(
            _env_file=None, auth_dev_mode=True, webhook_dev_mode=True, dashboard_public_url="https://e0.example"
        )
    )
    app.state.db = None
    app.state.asks_repo = FakeAsks()
    app.state.users_repo = FakeUsers()
    app.state.webhook_notifier = FakeNotifier()
    app.state.settings = FakeSettings({"asks": {"enabled": True}})
    open_decisions = {"offer:crack:1": dict(CRACK), "done:job-1": dict(JOB)}
    app.state.open_decisions = open_decisions

    async def get_decision(decision_id: str, _request: Any) -> dict[str, Any]:
        if decision_id not in open_decisions:
            raise HTTPException(status_code=404, detail="answered")
        return dict(open_decisions[decision_id])

    started: list[tuple[str, str]] = []
    app.state.started = started

    async def accept(proposal_id: str, body: Any, _request: Any) -> dict[str, Any]:
        started.append((proposal_id, body.by))
        open_decisions.pop(f"offer:{proposal_id}", None)
        return {"proposal_id": proposal_id, "decision": "accept", "by": body.by, "job_id": "job-9"}

    async def open_now(_request: Any) -> list[dict[str, Any]]:
        return [dict(d) for d in open_decisions.values()]

    monkeypatch.setattr(decisions_api, "get_decision", get_decision)
    monkeypatch.setattr(asks_api, "open_decisions", open_now)
    monkeypatch.setattr(proposals_api, "accept_proposal", accept)
    return app


async def call(app: Any, method: str, url: str, **kw: Any) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, url, headers=HEADERS, **kw)


@pytest.mark.asyncio
async def test_the_feed_gives_each_due_ask_its_card_code_person_and_chat_actions(app: Any) -> None:
    app.state.settings = FakeSettings({"asks": {"enabled": True, **ALWAYS}})
    app.state.asks_repo.rows = [
        ask("a1", "offer:crack:1", "ada@example.com"),
        ask("a2", "done:job-1", "github:bo"),
        ask("a3", "offer:gone:9", "ada@example.com"),
        ask("a4", "offer:crack:1", "cy@example.com", due_at=NOW + timedelta(hours=3)),
    ]
    app.state.asks_repo.codes["offer:gone:9"] = "GONE"
    due = (await call(app, "GET", "/api/v1/asks/due")).json()["asks"]
    assert [a["id"] for a in due] == ["a1", "a2"], "the answered one is left out, the later one waits"
    assert due[0]["code"] == "ABCD" and due[0]["recipient"] == {
        "email": "ada@example.com",
        "name": "Ada",
        "github_login": "ada",
    }
    assert due[0]["actions"] == ["start", "decline", "later", "send_to"]
    assert due[0]["dashboard_url"] == "https://e0.example/decisions?stage=offered"
    assert due[1]["recipient"]["github_login"] == "bo" and due[1]["actions"] == ["send_to"], (
        "no merge without an approval"
    )
    assert app.state.asks_repo.rows[2]["state"] == "queued", (
        "the feed withdraws nothing: a key may not see every decision, and the ask loop sees them all"
    )


@pytest.mark.asyncio
async def test_the_feed_is_empty_while_asks_are_off_even_with_live_asks_stored(app: Any) -> None:
    app.state.asks_repo.rows = [
        ask("a1", "offer:crack:1", "ada@example.com"),
        ask("a2", "done:job-1", "github:bo", state="sent", sent_at=NOW - timedelta(days=2)),
    ]
    app.state.settings = FakeSettings({"asks": {"enabled": False, **ALWAYS}})
    off = await call(app, "GET", "/api/v1/asks/due")
    assert off.status_code == 200, off.text
    assert off.json()["asks"] == [], "asks off means none is handed to a chat to send, queued or due a reminder"
    assert off.json()["enabled"] is False
    assert [r["state"] for r in app.state.asks_repo.rows] == ["queued", "sent"], "they wait, untouched"

    app.state.settings.data = {"asks": {"enabled": True, **ALWAYS}}
    on = (await call(app, "GET", "/api/v1/asks/due")).json()
    assert [a["id"] for a in on["asks"]] == ["a2", "a1"] and on["enabled"] is True, (
        "the same app, turned on in the document: the next request serves them, the reminder first"
    )


@pytest.mark.asyncio
async def test_the_feed_reads_the_document_on_each_request(app: Any) -> None:
    app.state.asks_repo.rows = [
        ask("a1", "offer:crack:1", "ada@example.com", state="sent", sent_at=NOW - timedelta(hours=30))
    ]
    always = {"enabled": True, **ALWAYS}
    app.state.settings = FakeSettings({"asks": {**always, "remind_hours": 48}})
    assert (await call(app, "GET", "/api/v1/asks/due")).json()["asks"] == [], "sent 30 hours ago, reminded after 48"
    assert app.state.settings.reads == 1, "one snapshot per request"
    app.state.settings.data = {"asks": {**always, "remind_hours": 24}}
    due = (await call(app, "GET", "/api/v1/asks/due")).json()["asks"]
    assert [(a["id"], a["reminder"]) for a in due] == [("a1", True)], "remind_hours 24, read on the next request"


@pytest.mark.asyncio
async def test_a_sent_report_moves_the_ask_on_and_a_later_one_counts_as_the_reminder(app: Any) -> None:
    app.state.asks_repo.rows = [ask("a1", "offer:crack:1", "ada@example.com")]
    first = (await call(app, "POST", "/api/v1/asks/a1/sent", json={"message_ref": "msg-1"})).json()
    assert first["state"] == "sent" and first["message_ref"] == "msg-1"
    copy = (await call(app, "POST", "/api/v1/asks/a1/sent", json={})).json()
    assert (copy["state"], copy["reminders"]) == ("sent", 0), "a second poll's copy right after it is not the reminder"
    app.state.asks_repo.rows[0]["sent_at"] -= timedelta(days=1)
    second = (await call(app, "POST", "/api/v1/asks/a1/sent", json={})).json()
    assert second["state"] == "reminded" and second["reminders"] == 1
    assert (await call(app, "POST", "/api/v1/asks/a1/sent", json={})).status_code == 409


@pytest.mark.asyncio
async def test_a_press_answers_once_through_the_dashboard_s_path_and_a_second_press_returns_the_first(app: Any) -> None:
    app.state.asks_repo.rows = [ask("a1", "offer:crack:1", "ada@example.com", state="sent", message_ref="msg-1")]
    first = await call(
        app, "POST", "/api/v1/asks/answer", json={"code": "abcd", "action": "start", "person": "ada@example.com"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["answer"] == "start" and first.json()["result"]["job_id"] == "job-9"
    assert app.state.started == [("crack:1", "ada@example.com")]
    assert first.json()["asks"] == [{"id": "a1", "recipient": "ada@example.com", "message_ref": "msg-1"}]
    assert app.state.webhook_notifier.sent[-1][0] == "ask.answered"
    again = (
        await call(
            app, "POST", "/api/v1/asks/answer", json={"code": "ABCD", "action": "decline", "person": "ada@example.com"}
        )
    ).json()
    assert again == {"already": True, "answer": "start", "by": "ada@example.com", "asks": []}
    assert app.state.started == [("crack:1", "ada@example.com")], "nothing ran twice"


@pytest.mark.asyncio
async def test_only_an_owner_the_hand_off_s_person_or_an_admin_may_answer(app: Any) -> None:
    body = {"code": "ABCD", "action": "start"}
    assert (
        await call(app, "POST", "/api/v1/asks/answer", json={**body, "person": "cy@example.com"})
    ).status_code == 403
    assert (
        await call(app, "POST", "/api/v1/asks/answer", json={**body, "person": "stranger@example.com"})
    ).status_code == 403
    app.state.open_decisions["offer:crack:1"]["assigned_to"] = {"email": "cy@example.com"}
    assert (
        await call(app, "POST", "/api/v1/asks/answer", json={**body, "person": "ada@example.com"})
    ).status_code == 403
    assert (
        await call(app, "POST", "/api/v1/asks/answer", json={**body, "person": "cy@example.com"})
    ).status_code == 200
    assert (
        await call(
            app, "POST", "/api/v1/asks/answer", json={"code": "QQQQ", "action": "start", "person": "x@example.com"}
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_a_press_may_not_name_an_embry0_actor(app: Any) -> None:
    """The person is who the answer is recorded as and who starts the work; an embry0 actor's name is not a person's."""
    resp = await call(
        app, "POST", "/api/v1/asks/answer", json={"code": "ABCD", "action": "start", "person": "embry0:scout"}
    )
    assert resp.status_code == 422, f"a press naming an embry0 actor is refused, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_a_chat_cannot_take_an_action_the_dashboard_keeps(app: Any) -> None:
    resp = await call(
        app, "POST", "/api/v1/asks/answer", json={"code": "WXYZ", "action": "merge", "person": "ada@example.com"}
    )
    assert resp.status_code == 409 and "not open on that quest from a chat" in resp.text


def _decide(app: Any, *decisions: tuple[str, str]) -> None:
    """Make ``(decision id, repository)`` the open decisions, in that order (worst first), each with a code."""
    app.state.open_decisions.clear()
    for i, (decision_id, repo) in enumerate(decisions):
        app.state.open_decisions[decision_id] = {**CRACK, "id": decision_id, "repo": repo}
        app.state.asks_repo.codes[decision_id] = f"C{i:03d}"


@pytest.mark.asyncio
async def test_a_person_s_open_asks_count_per_organization(app: Any) -> None:
    """Spec 2026-10-29, section 11: a person asked about two organizations' decisions holds each one's number."""
    _decide(
        app,
        ("offer:acme:1", "acme/web"),
        ("offer:acme:2", "acme/api"),
        ("offer:globex:1", "globex/gizmos"),
        ("offer:none:1", ""),
    )
    app.state.settings = LayeredSettings({"enabled": True, **ALWAYS, "max_open_per_person": 1}, {})
    app.state.asks_repo.rows = [
        ask("held", "offer:acme:1", "ada@example.com", state="sent", sent_at=NOW - timedelta(hours=1)),
        ask("acme", "offer:acme:2", "ada@example.com"),
        ask("globex", "offer:globex:1", "ada@example.com"),
        ask("none", "offer:none:1", "ada@example.com"),
    ]
    due = (await call(app, "GET", "/api/v1/asks/due")).json()["asks"]
    assert [a["id"] for a in due] == ["globex", "none"], (
        "ada holds acme's one unanswered ask, so acme's next waits; globex's, and the one about a decision "
        f"with no repository, each count apart and go out: {[a['id'] for a in due]}"
    )


@pytest.mark.asyncio
async def test_the_feed_holds_each_ask_to_its_organization_s_layer(app: Any) -> None:
    """Reminder hours, the reminder delay and the per-person limit each come from the ask's organization.

    The queued asks are all within their organization's working hours, which
    they need to go out (#276).
    """
    elsewhere = DAYS[(NOW.weekday() + 3) % 7]
    _decide(
        app,
        ("offer:acme:1", "acme/web"),
        ("offer:acme:2", "acme/web"),
        ("offer:globex:1", "globex/gizmos"),
        ("offer:initech:1", "initech/tools"),
        ("offer:none:1", ""),
    )
    app.state.settings = LayeredSettings(
        {"enabled": True, **ALWAYS, "remind_hours": 24, "max_open_per_person": 3},
        {"acme": {"remind_hours": 48, "max_open_per_person": 1}, "globex": {"weekdays": elsewhere}},
    )
    sent = {"state": "sent", "sent_at": NOW - timedelta(hours=30)}
    app.state.asks_repo.rows = [
        ask("remind-acme", "offer:acme:1", "ada@example.com", **sent),
        ask("remind-globex", "offer:globex:1", "ada@example.com", **sent),
        ask("remind-none", "offer:none:1", "ada@example.com", **sent),
        ask("queued-acme", "offer:acme:2", "ada@example.com"),
        ask("queued-initech", "offer:initech:1", "ada@example.com"),
    ]
    due = (await call(app, "GET", "/api/v1/asks/due")).json()["asks"]
    assert [(a["id"], a["reminder"]) for a in due] == [("remind-none", True), ("queued-initech", False)], (
        "sent 30 hours ago: acme reminds after 48 hours, and today is not one of globex's working days, so "
        "only the ask with no repository is reminded, by the deployment's 24 hours; acme's limit of one is "
        "held, and initech, with no layer, counts its own asks against the deployment's three: "
        f"{[(a['id'], a['reminder']) for a in due]}"
    )


class FrozenClock(datetime):
    """``datetime`` whose ``now()`` is :attr:`at`: the feed reads the real clock."""

    at = NOW

    @classmethod
    def now(cls, tz: Any = None) -> Any:
        return cls.at.astimezone(tz) if tz is not None else cls.at


@pytest.mark.asyncio
async def test_a_queued_ask_freed_outside_working_hours_stays_queued_until_the_next_working_time(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#276: ada answers acme's one ask at night in Tokyo; the ask behind it goes out on the next working morning."""
    monkeypatch.setattr(asks_api, "datetime", FrozenClock)
    _decide(app, ("offer:acme:1", "acme/web"), ("offer:acme:2", "acme/web"))
    app.state.settings = LayeredSettings(
        {"enabled": True, "max_open_per_person": 1}, {"acme": {"timezone": "Asia/Tokyo"}}
    )
    friday_10_jst = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)
    held = ask("held", "offer:acme:1", "ada@example.com", state="sent", sent_at=friday_10_jst, due_at=friday_10_jst)
    behind = ask("behind", "offer:acme:2", "ada@example.com", due_at=friday_10_jst, created_at=friday_10_jst)
    app.state.asks_repo.rows = [held, behind]

    FrozenClock.at = datetime(2026, 10, 30, 7, 0, tzinfo=UTC)  # Friday 16:00 in Tokyo
    due = [a["id"] for a in (await call(app, "GET", "/api/v1/asks/due")).json()["asks"]]
    assert due == [], f"ada holds acme's one ask, so the ask behind it waits: {due}"

    held["state"] = "answered"
    app.state.open_decisions.pop("offer:acme:1")
    FrozenClock.at = datetime(2026, 10, 30, 17, 0, tzinfo=UTC)  # Saturday 02:00 in Tokyo
    due = [a["id"] for a in (await call(app, "GET", "/api/v1/asks/due")).json()["asks"]]
    assert due == [], (
        "ada answered at 02:00 on Saturday in Tokyo, freeing her slot outside acme's working hours: "
        f"the ask behind it was due on Friday but waits for acme's working time: {due}"
    )
    assert behind["state"] == "queued", "it stays queued"

    FrozenClock.at = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)  # Monday 09:00 in Tokyo
    due = [a["id"] for a in (await call(app, "GET", "/api/v1/asks/due")).json()["asks"]]
    assert due == ["behind"], f"it goes out at acme's next working time, 09:00 on Monday in Tokyo: {due}"
