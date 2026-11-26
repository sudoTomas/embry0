"""Who may answer a decision from a chat, and who reads a person's handles, on the real people and users (#179).

- A caller owns a decision only as the person the directory names for one of its owners: a user unlinked from
  their person owns nothing, nor does a user who signs in with no person, and nobody does once that person is
  disabled.
- A disabled person answers nothing, a group's decision included, whether or not they have a sign-in; a disabled
  user is no user, and a person whose sign-in is disabled is disabled.
- A person's chat handles and hours in ``GET /asks/due`` go to whoever may read people and to the assistant's
  propose key, not to a read key.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.api.v1 import asks as asks_api
from embry0.api.v1 import decisions as decisions_api
from embry0.services.owner_people import directory_of
from tests.fixtures.repository_writes import repository_writes_app

pytestmark = pytest.mark.requires_postgres

ALICE = {"id": "offer:x:1", "repo": "acme/web", "owners": ["alice"], "assigned_to": None, "stage": "offered"}


def _group(name: str) -> Any:
    async def ask_policy(_request: Any) -> Any:
        return SimpleNamespace(deployment=lambda: SimpleNamespace(unowned_group=name))

    return ask_policy


async def _users(db: Any, *rows: tuple[str, str, str]) -> None:
    for user_id, email, tier in rows:
        await db.execute(
            "INSERT INTO users (id, auth0_sub, email, name, tier) VALUES ($1, $1, $2, $1, $3)", user_id, email, tier
        )


async def test_a_user_unlinked_from_their_person_owns_nothing(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        people, req = app.state.people_repo, SimpleNamespace(app=app)
        await _users(app.state.db, ("usr-u", "u@x.example", "operator"))
        monkeypatch.setattr(asks_api, "ask_policy", _group(""))
        alice = await people.create(
            {"name": "Alice", "email": "alice@corp.example", "user_id": "usr-u"},
            identities=[("github", "alice")],
            actor="t",
        )
        assert await asks_api._may_decide(req, ALICE, "u@x.example"), "linked, the user is Alice and owns her quest"
        await people.update(alice.id, {"user_id": None}, actor="t", expect_updated_at=alice.updated_at)
        unlinked = {who: await asks_api._may_decide(req, ALICE, who) for who in ("u@x.example", "alice@corp.example")}
        monkeypatch.setattr(decisions_api, "request_actor", lambda _r: "u@x.example")
        mine = await decisions_api.mine(req, [ALICE])
        alice = await people.get(alice.id)
        assert alice is not None
        await people.update(alice.id, {"disabled": True}, actor="t", expect_updated_at=alice.updated_at)
        disabled = {who: await asks_api._may_decide(req, ALICE, who) for who in ("u@x.example", "alice@corp.example")}
    assert unlinked == {"u@x.example": False, "alice@corp.example": True}, (
        f"the user unlinked from Alice owns what she owns: {unlinked}"
    )
    assert mine == [], "nor does the user see it as theirs"
    assert disabled == {"u@x.example": False, "alice@corp.example": False}, (
        f"with Alice disabled, someone still answers what she owns: {disabled}"
    )


async def test_a_disabled_person_or_user_answers_nothing_a_group_s_decision_included(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        people, users, req = app.state.people_repo, app.state.users_repo, SimpleNamespace(app=app)
        await _users(
            app.state.db,
            ("usr-gone", "gone@x.example", "operator"),
            ("usr-v", "v@x.example", "operator"),
            ("usr-adm", "adm@x.example", "admin"),
        )
        gone = await people.create(
            {"name": "Gone", "email": "gone@x.example", "user_id": "usr-gone"},
            identities=[("github", "gone")],
            actor="t",
        )
        await people.update(gone.id, {"disabled": True}, actor="t", expect_updated_at=gone.updated_at)
        theirs = {**ALICE, "id": "offer:x:2", "owners": ["gone"]}
        nobody_s = {**ALICE, "id": "offer:x:3", "owners": []}
        monkeypatch.setattr(asks_api, "ask_policy", _group("triage"))
        await users.set_disabled("usr-v", True)
        await users.set_disabled("usr-adm", True)
        answered = {
            "the disabled person, signing in, on the group's decision they own": await asks_api._may_decide(
                req, theirs, "gone@x.example"
            ),
            "the disabled person on another group decision": await asks_api._may_decide(
                req, nobody_s, "gone@x.example"
            ),
            "a disabled user on a group decision": await asks_api._may_decide(req, nobody_s, "v@x.example"),
            "a disabled admin": await asks_api._may_decide(req, ALICE, "adm@x.example"),
        }
        handed = {**nobody_s, "assigned_to": {"email": "gone@x.example"}}
        answered["the disabled person it was handed to"] = await asks_api._may_decide(req, handed, "gone@x.example")
    assert not any(answered.values()), f"disabled, and still answering: {[k for k, v in answered.items() if v]}"


async def test_a_person_whose_sign_in_is_disabled_is_neither_asked_nor_an_owner(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        people, users, req = app.state.people_repo, app.state.users_repo, SimpleNamespace(app=app)
        await _users(app.state.db, ("usr-w", "w@x.example", "operator"))
        await people.create(
            {"name": "W", "email": "w@x.example", "user_id": "usr-w"}, identities=[("github", "wee")], actor="t"
        )
        await users.set_disabled("usr-w", True)
        monkeypatch.setattr(asks_api, "ask_policy", _group(""))
        directory = await directory_of(app.state, ["acme/web"])
        decides = await asks_api._may_decide(req, {**ALICE, "owners": ["wee"]}, "w@x.example")
    assert directory.recipient("acme/web", "wee") is None, "a person whose sign-in is disabled is asked again"
    assert not decides, "a person whose sign-in is disabled answers what they own"


async def test_a_user_who_signs_in_with_no_person_owns_nothing(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Since ``users.github_login`` was retired (#179), a user owns a decision only as a person an admin made: one who
    signs in with no person is not asked as an owner, and answers nothing an owner may."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        req = SimpleNamespace(app=app)
        await _users(app.state.db, ("usr-s", "s@x.example", "operator"))
        monkeypatch.setattr(asks_api, "ask_policy", _group(""))
        directory = await directory_of(app.state, ["acme/web"])
        decides = await asks_api._may_decide(req, {**ALICE, "owners": ["kit"]}, "s@x.example")
    assert directory.recipient("acme/web", "kit") == "github:kit", "kit, whom no person holds, is asked as itself"
    assert not decides, "a user with no person owns nothing"


def _request(*, kind: str = "key", tier: str = "", allowed: tuple[str, ...] | None = None, session: Any = None) -> Any:
    principal = (
        SimpleNamespace(kind=kind, tier=tier, allowed_repos=allowed, key_name="k") if tier or kind == "master" else None
    )
    config = SimpleNamespace(auth_dev_mode=False)
    return SimpleNamespace(
        state=SimpleNamespace(principal=principal, session=session),
        app=SimpleNamespace(state=SimpleNamespace(config=config)),
    )


@pytest.mark.parametrize(
    ("caller", "reads"),
    [
        (_request(tier="read"), False),
        (_request(tier="merge"), False),
        (_request(tier="propose"), True),
        (_request(tier="operator"), True),
        (_request(tier="admin"), True),
        (_request(kind="master", tier="admin"), True),
        (_request(tier="propose", allowed=("acme/web",)), False),
        (_request(session=SimpleNamespace(tier="operator", email="o@x.example")), True),
        (_request(session=SimpleNamespace(tier="none", email="n@x.example")), False),
    ],
    ids=["read", "merge", "propose", "operate", "admin", "master", "an allowlist", "an operator session", "no role"],
)
def test_a_person_s_handles_and_hours_go_to_who_may_read_people_and_the_assistant(caller: Any, reads: bool) -> None:
    reads_people = getattr(asks_api, "reads_people", None)
    assert reads_people is not None, "a person's handles go to whoever reads the feed: nothing says who may read people"
    assert reads_people(caller) is reads, (
        f"GET /asks/due serves a person's chat handles and hours to whom GET /people answers, and to the assistant's "
        f"propose key, never through an allowlist: expected {reads}"
    )
