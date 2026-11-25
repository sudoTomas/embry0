"""The asks feed and answers, with people (#179).

- ``GET /asks/due``: a login no person holds is ``unknown``; a caller with no repository allowlist reads the
  person's chat handles and the time zone their ask is timed by (a limited key's view is in
  ``test_asks_restricted_key.py``).
- ``POST /asks/answer``: any person the assistant verified by email may answer what their identities own on the
  decision's forge, signed in or not; a disabled person may not; acting as admin still needs a signed-in admin; a
  decision nobody owns that goes to a group is anyone's embry0 knows, a person included.
- ``GET /decisions?for=me`` lists what the viewer's person owns, by the same identities.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.api.v1 import decisions as decisions_api
from tests.fixtures.people_directory import person
from tests.unit.api.v1.test_asks_api import ALWAYS, CRACK, FakeSettings, app, ask, call  # noqa: F401 - app: fixture

ADA = person(
    "Ada",
    email="ada@example.com",
    user_id="usr-ada",
    identities=[("github", "ada"), ("telegram", "ada_tg"), ("discord", "ada#1")],
    timezone="Europe/Paris",
)
#: Asked, never signed in: a person with an email and no user.
PAT = person("Pat", email="pat@example.com", identities=[("github", "pat"), ("gitlab", "gil")])
GONE = person("Gone", email="gone@example.com", identities=[("github", "gone")], disabled=True)


class _People:
    def __init__(self, *people: Any) -> None:
        self.people = people

    async def all(self, *, include_disabled: bool = False) -> list[Any]:
        return [p for p in self.people if include_disabled or not p.disabled]


class _Organizations:
    def __init__(self, **forges: str) -> None:
        self.forges = forges

    async def list_all(self) -> list[Any]:
        return [SimpleNamespace(owner=owner, forge=forge) for owner, forge in self.forges.items()]


def _people(
    app: Any,  # noqa: F811 - the app, not the fixture
    *people: Any,
    **forges: str,
) -> None:
    app.state.people_repo = _People(*people)
    app.state.organizations_repo = _Organizations(**(forges or {"acme": "github"}))


@pytest.mark.asyncio
async def test_the_feed_names_an_unknown_login_and_gives_a_person_s_chat_and_time_zone(
    app: Any,  # noqa: F811 - the imported fixture
) -> None:
    _people(app, ADA)
    app.state.settings = FakeSettings({"asks": {"enabled": True, **ALWAYS}})
    app.state.asks_repo.rows = [ask("a1", "offer:crack:1", "ada@example.com"), ask("a2", "done:job-1", "github:bo")]
    due = {a["id"]: a["recipient"] for a in (await call(app, "GET", "/api/v1/asks/due")).json()["asks"]}
    assert due["a1"].get("unknown") is False and due["a1"]["name"] == "Ada", f"Ada is a person: {due['a1']}"
    assert due["a1"].get("chat") == [{"channel": "telegram", "id": "ada_tg"}, {"channel": "discord", "id": "ada#1"}], (
        f"a caller with no allowlist reads the person's chat handles: {due['a1']}"
    )
    assert due["a1"].get("timezone") == "Europe/Paris", "the time zone Ada's ask is timed by: her own"
    assert (due["a2"].get("unknown"), due["a2"]["github_login"], due["a2"].get("chat")) == (True, "bo", []), (
        f"bo, whom no person holds, is asked as github:bo, for someone to add: {due['a2']}"
    )


async def _answer(
    app: Any,  # noqa: F811 - the app, not the fixture
    person_email: str,
    *,
    owners: list[str],
    repo: str = "acme/web",
) -> Any:
    app.state.open_decisions["offer:crack:1"] = {**CRACK, "owners": owners, "repo": repo}
    app.state.settings = FakeSettings({"asks": {"enabled": True, **ALWAYS}})
    body = {"code": "ABCD", "action": "start", "person": person_email}
    return await call(app, "POST", "/api/v1/asks/answer", json=body)


@pytest.mark.asyncio
async def test_a_person_who_never_signed_in_answers_what_their_identity_owns(
    app: Any,  # noqa: F811 - the imported fixture
) -> None:
    _people(app, PAT)
    answered = await _answer(app, "pat@example.com", owners=["@Pat"])
    assert answered.status_code == 200, (
        f"Pat, asked and verified by email, owns the quest by their GitHub identity: {answered.text}"
    )


@pytest.mark.asyncio
async def test_an_owner_of_a_gitlab_organization_s_decision_is_a_gitlab_login(
    app: Any,  # noqa: F811 - the imported fixture
) -> None:
    _people(app, PAT, acme="gitlab")
    assert (await _answer(app, "pat@example.com", owners=["gil"])).status_code == 200, "gitlab:gil is Pat's"


@pytest.mark.asyncio
async def test_a_github_login_owns_nothing_of_a_gitlab_organization_s_decision(
    app: Any,  # noqa: F811 - the imported fixture
) -> None:
    _people(app, PAT, acme="gitlab")
    refused = await _answer(app, "pat@example.com", owners=["pat"])
    assert refused.status_code == 403, f"Pat's GitHub login owns nothing of a GitLab organization's: {refused.text}"


@pytest.mark.asyncio
async def test_a_disabled_person_and_a_stranger_may_not_answer_and_an_admin_still_may(
    app: Any,  # noqa: F811 - the imported fixture
) -> None:
    _people(app, GONE, PAT)
    assert (await _answer(app, "gone@example.com", owners=["gone"])).status_code == 403, "a disabled person is no owner"
    assert (await _answer(app, "pat@example.com", owners=["someone"])).status_code == 403, "Pat owns none of it"
    assert (await _answer(app, "nobody@example.com", owners=["someone"])).status_code == 403
    assert (await _answer(app, "root@example.com", owners=["someone"])).status_code == 200, "a signed-in admin may"


@pytest.mark.asyncio
async def test_a_group_s_decision_is_any_known_person_s(
    app: Any,  # noqa: F811 - the imported fixture
) -> None:
    _people(app, PAT, GONE)
    app.state.open_decisions["offer:crack:1"] = {**CRACK, "owners": ["gone"]}
    app.state.settings = FakeSettings({"asks": {"enabled": True, "unowned_group": "triage", **ALWAYS}})
    body = {"code": "ABCD", "action": "start", "person": "pat@example.com"}
    stranger = {**body, "person": "nobody@example.com"}
    assert (await call(app, "POST", "/api/v1/asks/answer", json=stranger)).status_code == 403, (
        "someone embry0 does not know may not answer a group's decision"
    )
    disabled = {**body, "person": "gone@example.com"}
    assert (await call(app, "POST", "/api/v1/asks/answer", json=disabled)).status_code == 403, (
        "a disabled person is no one embry0 asks, nor one who answers for a group"
    )
    answered = await call(app, "POST", "/api/v1/asks/answer", json=body)
    assert answered.status_code == 200, (
        f"owned only by a disabled person, the decision goes to the group, and Pat, whom embry0 knows, may answer: "
        f"{answered.text}"
    )


@pytest.mark.asyncio
async def test_waiting_for_me_is_what_my_person_s_identities_own(
    app: Any,  # noqa: F811 - the imported fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _people(app, PAT, acme="gitlab")
    monkeypatch.setattr(decisions_api, "request_actor", lambda _r: "pat@example.com")
    shown = [
        {**CRACK, "id": "offer:mine:1", "owners": ["gil"], "assigned_to": None},
        {**CRACK, "id": "offer:not:1", "owners": ["pat"], "assigned_to": None},
    ]
    mine = await decisions_api.mine(SimpleNamespace(app=app), shown)
    assert [d["id"] for d in mine] == ["offer:mine:1"], (
        "on a GitLab organization's quest, Pat's GitLab identity owns it and their GitHub login owns nothing"
    )


@pytest.mark.asyncio
async def test_a_disabled_person_answers_nothing_even_what_was_handed_to_them(
    app: Any,  # noqa: F811 - the imported fixture
) -> None:
    _people(app, GONE)
    app.state.open_decisions["offer:crack:1"] = {
        **CRACK,
        "owners": ["gone"],
        "assigned_to": {"email": "gone@example.com"},
    }
    app.state.settings = FakeSettings({"asks": {"enabled": True, **ALWAYS}})
    body = {"code": "ABCD", "action": "start", "person": "gone@example.com"}
    refused = await call(app, "POST", "/api/v1/asks/answer", json=body)
    assert refused.status_code == 403, f"a disabled person answered what was handed to them: {refused.text}"


@pytest.mark.asyncio
async def test_a_dashboard_session_answers_only_as_the_person_signed_in(
    app: Any,  # noqa: F811 - the imported fixture
) -> None:
    """A key names the person the assistant verified; a session is someone already, and answers as them alone."""
    import os
    from unittest.mock import AsyncMock, MagicMock, patch

    from httpx import ASGITransport, AsyncClient

    from embry0.api.deps import bust_api_key_cache, bust_user_gate_cache
    from embry0.api.session import SESSION_COOKIE, SessionData, encode_session
    from embry0.config import Embry0Config

    secret = "s" * 48
    with patch.dict(os.environ, {}, clear=True):
        app.state.config = Embry0Config(
            _env_file=None,
            auth_dev_mode=False,
            api_key="master-key",
            auth0_issuer="https://t.example.com/",
            auth0_client_id="cid",
            auth0_client_secret="sec",
            session_secret=secret,
            sso_public_origin="https://e.example.com",
            sso_roles_claim="https://example.com/roles",
        )
    signed_in = MagicMock(id="usr-ada", email="ada@example.com", session_epoch=1, disabled=False, tier="operator")
    app.state.users_repo.get_by_sub = AsyncMock(return_value=signed_in)
    app.state.users_repo.get = AsyncMock(return_value=signed_in)
    bust_api_key_cache()
    bust_user_gate_cache()
    app.state.settings = FakeSettings({"asks": {"enabled": True, **ALWAYS}})
    cookie = encode_session(
        secret,
        SessionData(
            sub="auth0|ada", email="ada@example.com", name="Ada", roles=["embry0-operator"], tier="operator", epoch=1
        ),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        as_other = await client.post(
            "/api/v1/asks/answer",
            json={"code": "ABCD", "action": "start", "person": "root@example.com"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        as_self = await client.post(
            "/api/v1/asks/answer",
            json={"code": "ABCD", "action": "start", "person": "ada@example.com"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    bust_api_key_cache()
    bust_user_gate_cache()
    assert as_other.status_code == 403 and "signed in" in as_other.json()["detail"], (
        f"a session answered as an admin it is not: {as_other.status_code} {as_other.text}"
    )
    assert as_self.status_code == 200, f"Ada, signed in, could not answer her own quest: {as_self.text}"
