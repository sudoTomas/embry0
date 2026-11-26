"""Hand-offs (spec 2026-10-27): send a decision to a teammate, who then decides it."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import decisions as decisions_api
from embry0.api.v1 import handoffs as handoffs_api
from embry0.config import Embry0Config
from embry0.services.handoffs import settle
from tests.fixtures.people_directory import person

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
ME = "ada@example.com"


def user(uid: str, email: str, *, name: str = "", tier: str = "operator") -> Any:
    return SimpleNamespace(id=uid, email=email, name=name, tier=tier, disabled=False)


class FakeUsers:
    def __init__(self) -> None:
        self.rows = [
            user("u1", ME, name="Ada"),
            user("u2", "bo@example.com", name="Bo"),
            user("u3", "guest@example.com", tier="none"),
            user("u4", "cy@example.com", name="Cy", tier="none"),
        ]

    async def people(self) -> list[Any]:
        return list(self.rows)

    async def disabled_ids(self) -> set[str]:
        return {u.id for u in self.rows if getattr(u, "disabled", False)}

    async def get_by_email(self, email: str) -> Any:
        return next((u for u in self.rows if u.email.lower() == email.lower()), None)


class FakePeople:
    """The people an admin made (#179): Ada, who signs in, and Cy and Dee, who do not (Cy's user may not)."""

    def __init__(self) -> None:
        self.rows = [
            person("Ada", email=ME, user_id="u1", identities=[("github", "ada")]),
            person("Cy", email="cy@example.com", user_id="u4", identities=[("github", "cy")]),
            person("Dee", email="dee@example.com"),
        ]

    async def all(self, *, include_disabled: bool = False) -> list[Any]:
        return [p for p in self.rows if include_disabled or not p.disabled]


class FakeHandoffs:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def send(self, **kw: Any) -> dict[str, Any]:
        for r in self.rows:
            if r["decision_id"] == kw["decision_id"] and r["state"] == "open":
                r["state"] = "passed"
        row = {"id": f"ho-{len(self.rows) + 1}", "state": "open", "outcome": None, "created_at": kw.pop("now"), **kw}
        self.rows.append(row)
        return dict(row)

    async def get(self, handoff_id: str) -> dict[str, Any] | None:
        return next((dict(r) for r in self.rows if r["id"] == handoff_id), None)

    async def open_for(self, ids: Any) -> dict[str, dict[str, Any]]:
        wanted = set(ids)
        return {r["decision_id"]: dict(r) for r in self.rows if r["state"] == "open" and r["decision_id"] in wanted}

    async def open_to(self, email: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows if r["state"] == "open" and r["to_email"] == email.lower()]

    async def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows][:limit]

    async def close(
        self, *, decision_id: Any = None, handoff_id: Any = None, state: str, outcome: Any, by: str, now: Any
    ) -> Any:
        for r in self.rows:
            if r["state"] == "open" and (r["decision_id"] == decision_id or r["id"] == handoff_id):
                r.update(state=state, outcome=outcome, closed_by=by)
                return dict(r)
        return None


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def emit_soon(self, event: str, data: dict[str, Any]) -> None:
        self.sent.append((event, data))


DECISIONS = [
    {
        "id": "offer:crack:1",
        "stage": "offered",
        "title": "A crack",
        "repo": "acme/web",
        "owners": ["ada"],
        "assigned_to": None,
    },
    {
        "id": "offer:issue:2",
        "stage": "offered",
        "title": "An issue",
        "repo": "acme/web",
        "owners": ["cy"],
        "assigned_to": None,
    },
    {"id": "done:job-1", "stage": "done", "title": "A job", "repo": "acme/web", "owners": [], "assigned_to": None},
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.db = None
    app.state.users_repo = FakeUsers()
    app.state.people_repo = FakePeople()
    app.state.handoffs_repo = FakeHandoffs()
    app.state.webhook_notifier = FakeNotifier()

    async def read_decision(_request: Any, decision_id: str, *, action: str | None = None) -> dict[str, Any]:
        found = next((d for d in DECISIONS if d["id"] == decision_id), None)
        if found is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="no such decision")
        return dict(found)

    async def offers(_request: Any, **_kw: Any) -> list[Any]:
        return []

    monkeypatch.setattr(decisions_api, "read_decision", read_decision)
    monkeypatch.setattr(handoffs_api, "request_actor", lambda _r: ME)
    monkeypatch.setattr(decisions_api, "request_actor", lambda _r: ME)
    return app


async def call(app: Any, method: str, url: str, **kw: Any) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, url, headers=HEADERS, **kw)


@pytest.mark.asyncio
async def test_the_people_a_decision_can_go_to_sign_in_or_are_people(app: Any) -> None:
    people = (await call(app, "GET", "/api/v1/handoffs/people")).json()["people"]
    assert [p["email"] for p in people] == [ME, "bo@example.com", "cy@example.com", "dee@example.com"], (
        f"everyone who signs in and every person, not a guest who is neither: {people}"
    )
    assert people[1] == {"id": "u2", "email": "bo@example.com", "name": "Bo", "tier": "operator"}, "no GitHub login"
    assert people[3] == {"id": "per-dee", "email": "dee@example.com", "name": "Dee", "tier": ""}, (
        "a person with no sign-in, by their own id"
    )


@pytest.mark.asyncio
async def test_a_decision_can_go_to_a_person_who_never_signed_in(app: Any) -> None:
    for to in ("cy@example.com", "dee@example.com"):
        resp = await call(app, "POST", "/api/v1/decisions/offer:crack:1/send-to", json={"to": to})
        assert resp.status_code == 201, resp.text
        assert resp.json()["to_email"] == to


@pytest.mark.asyncio
async def test_sending_a_decision_opens_a_hand_off_and_tells_the_chat(app: Any) -> None:
    resp = await call(
        app, "POST", "/api/v1/decisions/offer:crack:1/send-to", json={"to": "Bo@example.com", "note": "Your area"}
    )
    assert resp.status_code == 201, resp.text
    sent = resp.json()
    assert sent["to_email"] == "bo@example.com" and sent["from_actor"] == ME and sent["note"] == "Your area"
    assert sent["decision_id"] == "offer:crack:1" and sent["title"] == "A crack" and sent["state"] == "open"
    assert app.state.webhook_notifier.sent[0][0] == "handoff.created"
    again = await call(app, "POST", "/api/v1/decisions/offer:crack:1/send-to", json={"to": "bo@example.com"})
    assert again.status_code == 201
    assert [r["state"] for r in app.state.handoffs_repo.rows] == ["passed", "open"], "one open hand-off per decision"


@pytest.mark.asyncio
async def test_a_decision_goes_only_to_someone_else_who_signs_in(app: Any) -> None:
    to_self = await call(app, "POST", "/api/v1/decisions/offer:crack:1/send-to", json={"to": ME})
    assert to_self.status_code == 422 and "yours already" in to_self.text
    assert (
        await call(app, "POST", "/api/v1/decisions/offer:crack:1/send-to", json={"to": "guest@example.com"})
    ).status_code == 422
    assert (
        await call(app, "POST", "/api/v1/decisions/offer:crack:1/send-to", json={"to": "nobody@example.com"})
    ).status_code == 422
    assert (
        await call(app, "POST", "/api/v1/decisions/offer:nope/send-to", json={"to": "bo@example.com"})
    ).status_code == 404


@pytest.mark.asyncio
async def test_the_recipient_sends_it_back_with_a_note_and_the_sender_can_take_it_back(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await call(app, "POST", "/api/v1/decisions/offer:crack:1/send-to", json={"to": "bo@example.com"})
    monkeypatch.setattr(handoffs_api, "request_actor", lambda _r: "bo@example.com")
    mine = (await call(app, "GET", "/api/v1/handoffs?for=me")).json()["handoffs"]
    assert [h["decision_id"] for h in mine] == ["offer:crack:1"]
    back = await call(app, "POST", f"/api/v1/handoffs/{mine[0]['id']}/return", json={"note": "Not my code"})
    assert back.json()["state"] == "returned" and back.json()["outcome"] == "Not my code"
    monkeypatch.setattr(handoffs_api, "request_actor", lambda _r: ME)
    await call(app, "POST", "/api/v1/decisions/offer:crack:1/send-to", json={"to": "bo@example.com"})
    cancelled = await call(app, "POST", "/api/v1/handoffs/ho-2/cancel")
    assert cancelled.json()["state"] == "cancelled"
    assert (await call(app, "POST", "/api/v1/handoffs/ho-2/cancel")).status_code == 404


@pytest.mark.asyncio
async def test_nobody_names_a_github_login_of_their_own_any_more(app: Any) -> None:
    """The logins a person is named by are their identities, which an admin writes on the People page (#179)."""
    for url in ("/api/v1/me/github-login", "/api/v1/users/u2/github-login"):
        resp = await call(app, "PUT", url, json={"login": "ada-l"})
        assert resp.status_code == 404, f"{url} was retired with users.github_login: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_settling_closes_the_open_hand_off_as_decided_and_tells_the_sender(app: Any) -> None:
    await call(app, "POST", "/api/v1/decisions/done:job-1/send-to", json={"to": "bo@example.com"})
    closed = await settle(app.state, "done:job-1", "merge", "bo@example.com")
    assert closed is not None and closed["state"] == "decided" and closed["outcome"] == "merge"
    assert app.state.webhook_notifier.sent[-1][0] == "handoff.closed"
    assert await settle(app.state, "done:job-1", "merge", "bo@example.com") is None, "nothing left open"
    assert await settle(SimpleNamespace(), "done:job-1", "merge", "x") is None, "no repository, no error"


@pytest.mark.asyncio
async def test_waiting_for_me_is_what_was_handed_to_me_and_what_my_login_owns(app: Any) -> None:
    await call(app, "POST", "/api/v1/decisions/offer:issue:2/send-to", json={"to": "bo@example.com"})
    app.state.handoffs_repo.rows.append(
        {
            "id": "ho-9",
            "decision_id": "done:job-1",
            "to_email": ME,
            "from_actor": "bo@example.com",
            "note": "Please",
            "state": "open",
            "created_at": datetime.now(UTC),
            "repo": "acme/web",
            "title": "A job",
        }
    )
    shown = await decisions_api.with_assignees(SimpleNamespace(app=app), [dict(d) for d in DECISIONS])
    assert shown[2]["assigned_to"]["email"] == ME and shown[2]["assigned_to"]["note"] == "Please"
    assert shown[1]["assigned_to"]["name"] == "Bo"
    mine = await decisions_api.mine(SimpleNamespace(app=app), shown)
    assert [d["id"] for d in mine] == ["offer:crack:1", "done:job-1"], "owned by my login, and handed to me"
