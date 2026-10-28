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

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
NOW = datetime.now(UTC)


class FakeAsks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.codes = {"offer:crack:1": "ABCD", "done:job-1": "WXYZ"}

    async def live(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows if r["state"] in ("queued", "sent", "reminded")]

    async def code(self, decision_id: str) -> str:
        return self.codes[decision_id]

    async def decision_of(self, code: str) -> str | None:
        return next((d for d, c in self.codes.items() if c == code), None)

    async def close(self, ids: Any, *, state: str, now: datetime) -> int:
        for r in self.rows:
            if r["id"] in set(ids):
                r["state"] = state
        return 1

    async def sent(self, ask_id: str, *, message_ref: str, now: datetime) -> dict[str, Any] | None:
        for r in self.rows:
            if r["id"] == ask_id and r["state"] in ("queued", "sent"):
                r["reminders"] += 0 if r["state"] == "queued" else 1
                r["state"] = "sent" if r["state"] == "queued" else "reminded"
                r["message_ref"] = message_ref or r.get("message_ref")
                r.setdefault("sent_at", now)
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

    async def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows][:limit]


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
async def test_the_feed_gives_each_due_ask_its_card_code_person_and_chat_actions(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(asks_api, "next_working_time", lambda now, _s: now)
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
async def test_a_sent_report_moves_the_ask_on_and_a_second_counts_as_the_reminder(app: Any) -> None:
    app.state.asks_repo.rows = [ask("a1", "offer:crack:1", "ada@example.com")]
    first = (await call(app, "POST", "/api/v1/asks/a1/sent", json={"message_ref": "msg-1"})).json()
    assert first["state"] == "sent" and first["message_ref"] == "msg-1"
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
async def test_a_chat_cannot_take_an_action_the_dashboard_keeps(app: Any) -> None:
    resp = await call(
        app, "POST", "/api/v1/asks/answer", json={"code": "WXYZ", "action": "merge", "person": "ada@example.com"}
    )
    assert resp.status_code == 409 and "not open on that quest from a chat" in resp.text
