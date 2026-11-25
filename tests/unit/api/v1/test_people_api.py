"""The people API (#179 pull request 3): read, add, change and remove people, on the real app and Postgres.

Operators and admins read, admins write, and a key with a repository
allowlist reads nobody and writes nothing. A write with problems is one 422
naming each by where it is, never its value; a value someone else holds is a
409; a change guarded by If-Match is refused once the person changed. The
hand-off list moved to /handoffs/people. A user's login route makes no person,
says when a cleared login falls back, and a login is one person's: another
user's login is no person's identity, and another person's identity is no
user's login.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_writes import COVERED, MASTER, repository_writes_app

pytestmark = pytest.mark.requires_postgres

PEOPLE = "/api/v1/people"


async def _call(app: Any, method: str, url: str, token: str, body: Any = None, **headers: str) -> Any:
    sent = {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}
    sent.update({key.replace("_", "-"): value for key, value in headers.items()})
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=sent) as client:
        return await client.request(method, url, json=body)


@pytest.fixture
async def people_app(db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Any, Any]]:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        repo = ApiKeysRepository(app.state.db)
        _row, operator = await repo.create(name="operator", scope="operate", user_id=None, created_by="tests")
        _row, reader = await repo.create(name="reader", scope="read", user_id=None, created_by="tests")
        app.state.test_keys = {"operator": operator, "reader": reader, "limited": keys.limited}
        yield app, keys


async def _user(app: Any, user_id: str, email: str, login: str | None = None) -> None:
    await app.state.db.execute(
        "INSERT INTO users (id, auth0_sub, email, name, tier, github_login) VALUES ($1, $1, $2, '', 'operator', $3)",
        user_id,
        email,
        login,
    )


async def test_an_admin_adds_reads_changes_and_removes_a_person(people_app: tuple[Any, Any]) -> None:
    app, _keys = people_app
    made = await _call(
        app,
        "POST",
        PEOPLE,
        MASTER,
        {
            "name": "Ada",
            "email": "ada@example.com",
            "hours": "9-17",
            "identities": [{"kind": "github", "value": "ada-l"}],
        },
    )
    assert made.status_code == 201, made.text
    ada = made.json()
    assert (ada["name"], ada["hours"], ada["identities"]) == ("Ada", "09-17", [{"kind": "github", "value": "ada-l"}])
    etag = made.headers["etag"]
    listed = (await _call(app, "GET", PEOPLE, app.state.test_keys["operator"])).json()
    assert [p["id"] for p in listed["people"]] == [ada["id"]] and "created_by" not in listed["people"][0]
    one = await _call(app, "GET", f"{PEOPLE}/{ada['id']}", app.state.test_keys["operator"])
    assert one.status_code == 200 and one.headers["etag"] == etag

    changed = await _call(
        app,
        "PATCH",
        f"{PEOPLE}/{ada['id']}",
        MASTER,
        {"timezone": "Europe/London", "identities": [{"kind": "slack", "value": "U024BE7LH"}]},
        if_match=etag,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["identities"] == [{"kind": "slack", "value": "U024BE7LH"}], (
        "identities given are the person's"
    )
    stale = await _call(app, "PATCH", f"{PEOPLE}/{ada['id']}", MASTER, {"hours": None}, if_match=etag)
    assert stale.status_code == 409 and "read it again" in stale.json()["detail"], stale.text
    blind = await _call(app, "PATCH", f"{PEOPLE}/{ada['id']}", MASTER, {"identities": []})
    assert blind.status_code == 428 and "If-Match" in blind.json()["detail"], (
        f"a change without the version it read would take the identities given since (#691 review): {blind.text}"
    )
    kept = (await _call(app, "GET", f"{PEOPLE}/{ada['id']}", MASTER)).json()
    assert kept["identities"] == [{"kind": "slack", "value": "U024BE7LH"}], "the refused change changed nothing"

    given = await _call(app, "POST", f"{PEOPLE}/{ada['id']}/identities", MASTER, {"kind": "gitlab", "value": "ada"})
    assert given.status_code == 200 and {"kind": "gitlab", "value": "ada"} in given.json()["identities"]
    taken = await _call(app, "DELETE", f"{PEOPLE}/{ada['id']}/identities/gitlab/ADA", MASTER)
    assert taken.status_code == 200 and {"kind": "gitlab", "value": "ada"} not in taken.json()["identities"]
    assert (await _call(app, "DELETE", f"{PEOPLE}/{ada['id']}", MASTER)).status_code == 200
    assert (await _call(app, "GET", f"{PEOPLE}/{ada['id']}", MASTER)).status_code == 404


async def test_a_write_with_problems_names_every_one_by_where_and_never_its_value(people_app: tuple[Any, Any]) -> None:
    app, _keys = people_app
    body = {
        "email": "not-an-address-zz9",
        "hours": "18-09",
        "weekdays": "someday-zz9",
        "nickname": "Zed",
        "identities": [{"kind": "github", "value": "bad--login-zz9"}, {"kind": "myspace", "value": "zz9"}],
    }
    refused = await _call(app, "POST", PEOPLE, MASTER, body)
    assert refused.status_code == 422, refused.text
    where = sorted(".".join(map(str, item["loc"][1:])) for item in refused.json()["detail"])
    assert where == [
        "email",
        "hours",
        "identities.0.value",
        "identities.1.kind",
        "name",
        "nickname",
        "weekdays",
    ], f"every problem is named, by where it is: {where}"
    assert "zz9" not in refused.text and "Zed" not in refused.text, f"a 422 repeats a value: {refused.text}"
    twice = await _call(
        app,
        "POST",
        PEOPLE,
        MASTER,
        {"name": "Bo", "identities": [{"kind": "github", "value": "bo"}, {"kind": "github", "value": "BO"}]},
    )
    assert twice.status_code == 422 and [i["loc"] for i in twice.json()["detail"]] == [["body", "identities", 1]]


async def test_a_value_someone_else_holds_is_a_409(people_app: tuple[Any, Any]) -> None:
    app, _keys = people_app
    await _user(app, "usr-cy", "cy@example.com", "cy-login")
    first = await _call(app, "POST", PEOPLE, MASTER, {"name": "Ada", "email": "ada@example.com"})
    assert first.status_code == 201
    for body, field in (
        ({"name": "Ada again", "email": "ADA@example.com"}, "email"),
        ({"name": "Not Cy", "identities": [{"kind": "github", "value": "CY-login"}]}, "github login"),
    ):
        refused = await _call(app, "POST", PEOPLE, MASTER, body)
        assert refused.status_code == 409 and field in refused.json()["detail"], (field, refused.text)
    given = await _call(
        app, "POST", f"{PEOPLE}/{first.json()['id']}/identities", MASTER, {"kind": "github", "value": "cy-login"}
    )
    assert given.status_code == 409, f"another user's login was given to a person not theirs: {given.text}"
    made_theirs = await _call(
        app,
        "PATCH",
        f"{PEOPLE}/{first.json()['id']}",
        MASTER,
        {"identities": [{"kind": "github", "value": "Cy-Login"}]},
        if_match=first.headers["etag"],
    )
    assert made_theirs.status_code == 409 and "github login" in made_theirs.json()["detail"], (
        f"another user's login was made a person's identities: {made_theirs.text}"
    )


async def test_operators_read_admins_write_and_a_limited_key_reads_nobody(people_app: tuple[Any, Any]) -> None:
    app, keys = people_app
    ada = (await _call(app, "POST", PEOPLE, MASTER, {"name": "Ada"})).json()
    operator, reader = app.state.test_keys["operator"], app.state.test_keys["reader"]
    assert (await _call(app, "GET", PEOPLE, operator)).json()["count"] == 1
    assert (await _call(app, "GET", PEOPLE, reader)).status_code == 403, "a read key is no operator"
    assert (await _call(app, "POST", PEOPLE, operator, {"name": "Bo"})).status_code == 403, "operators only read"
    assert (await _call(app, "PATCH", f"{PEOPLE}/{ada['id']}", operator, {"name": "A"})).status_code == 403
    limited = keys.limited
    assert (await _call(app, "GET", PEOPLE, limited)).json() == {"people": [], "count": 0}
    assert (await _call(app, "GET", f"{PEOPLE}/{ada['id']}", limited)).status_code == 404
    assert (await _call(app, "GET", f"{PEOPLE}/suggestions", limited)).json() == {"users": [], "owners": []}
    assert (await _call(app, "POST", PEOPLE, limited, {"name": "Bo"})).status_code == 403
    assert (await _call(app, "DELETE", f"{PEOPLE}/{ada['id']}", limited)).status_code == 403


async def test_the_hand_off_list_moved_and_people_answers_the_directory(people_app: tuple[Any, Any]) -> None:
    app, _keys = people_app
    await _user(app, "usr-da", "da@example.com", "da-login")
    handoffs = (await _call(app, "GET", "/api/v1/handoffs/people", MASTER)).json()["people"]
    assert [p["email"] for p in handoffs] == ["da@example.com"], f"who a decision can be sent to: {handoffs}"
    assert (await _call(app, "GET", PEOPLE, MASTER)).json() == {"people": [], "count": 0}, "nobody is a person yet"


async def test_suggestions_list_users_with_no_person_and_owner_logins_nobody_holds(
    people_app: tuple[Any, Any],
) -> None:
    app, _keys = people_app
    await _user(app, "usr-ed", "ed@example.com")
    await app.state.db.execute(
        "INSERT INTO repo_preferences (repo, owners) VALUES ($1, $2) ON CONFLICT (repo) DO UPDATE SET owners = $2",
        COVERED,
        ["owner-one", "Owner-Two"],
    )
    suggested = (await _call(app, "GET", f"{PEOPLE}/suggestions", MASTER)).json()
    assert "usr-ed" in [u["user_id"] for u in suggested["users"]]
    owners = {o["login"]: (o.get("forge"), o["repos"]) for o in suggested["owners"]}
    assert (owners.get("owner-one"), owners.get("owner-two")) == (("github", [COVERED]), ("github", [COVERED])), (
        f"each owner login on its repository's forge, with the repositories naming it: {owners}"
    )
    await _call(
        app,
        "POST",
        PEOPLE,
        MASTER,
        {"name": "Ed", "user_id": "usr-ed", "identities": [{"kind": "github", "value": "OWNER-ONE"}]},
    )
    after = (await _call(app, "GET", f"{PEOPLE}/suggestions", MASTER)).json()
    assert "usr-ed" not in [u["user_id"] for u in after["users"]], "a user who is a person is no suggestion"
    still = [o["login"] for o in after["owners"]]
    assert "owner-one" not in still and "owner-two" in still, f"an owner a person holds is no suggestion: {still}"


async def test_suggestions_read_an_owner_on_its_organization_s_forge(people_app: tuple[Any, Any]) -> None:
    """An owner login is suggested on the forge of its repository's organization, through the deployment's own
    organizations: a GitLab organization's owner is a GitLab login, which a person's GitHub login of the same text
    does not hold and their GitLab login does."""
    app, _keys = people_app
    await app.state.organizations_repo.register(COVERED.split("/")[0], "gitlab", created_by="tests")
    await app.state.db.execute(
        "INSERT INTO repo_preferences (repo, owners) VALUES ($1, $2) ON CONFLICT (repo) DO UPDATE SET owners = $2",
        COVERED,
        ["lab-owner"],
    )
    await _call(app, "POST", PEOPLE, MASTER, {"name": "Hub", "identities": [{"kind": "github", "value": "lab-owner"}]})
    owners = {o["login"]: (o.get("forge"), o["repos"]) for o in (await _suggested(app))["owners"]}
    assert owners.get("lab-owner") == ("gitlab", [COVERED]), (
        f"the owner of a GitLab organization's repository is suggested as a GitLab login: {owners}"
    )
    await _call(app, "POST", PEOPLE, MASTER, {"name": "Lab", "identities": [{"kind": "gitlab", "value": "lab-owner"}]})
    still = [(o.get("forge"), o["login"]) for o in (await _suggested(app))["owners"]]
    assert ("gitlab", "lab-owner") not in still, f"a person's GitLab login holds the GitLab owner: {still}"


async def _suggested(app: Any) -> dict[str, Any]:
    answer = await _call(app, "GET", f"{PEOPLE}/suggestions", MASTER)
    assert answer.status_code == 200, f"the suggestions are read: {answer.status_code} {answer.text}"
    return dict(answer.json())


async def test_an_owner_is_suggested_on_its_forge_and_only_that_forge_s_identity_holds_it(
    people_app: tuple[Any, Any],
) -> None:
    """#181: an owner is a handle. A bare one is on its repository's forge, a named one on its own; a person holds
    it by an identity of that forge only."""
    app, _keys = people_app
    await app.state.repos_repo.register("lab/web", forge="gitlab")
    await app.state.db.execute(
        "INSERT INTO repo_preferences (repo, owners) VALUES ($1, $2) ON CONFLICT (repo) DO UPDATE SET owners = $2",
        "lab/web",
        ["Mira", "github:mira", "gitlab:bob_s"],
    )
    suggested = (await _call(app, "GET", f"{PEOPLE}/suggestions", MASTER)).json()["owners"]
    mine = {(o["forge"], o["login"]): o["repos"] for o in suggested if "lab/web" in o["repos"]}
    assert mine == {("gitlab", "mira"): ["lab/web"], ("github", "mira"): ["lab/web"], ("gitlab", "bob_s"): ["lab/web"]}
    made = await _call(
        app, "POST", PEOPLE, MASTER, {"name": "Mira", "identities": [{"kind": "gitlab", "value": "MIRA"}]}
    )
    assert made.status_code == 201, made.text
    after = (await _call(app, "GET", f"{PEOPLE}/suggestions", MASTER)).json()["owners"]
    left = {(o["forge"], o["login"]) for o in after if "lab/web" in o["repos"]}
    assert left == {("github", "mira"), ("gitlab", "bob_s")}, "a GitLab identity holds the GitLab owner alone"
    # The organization's forge wins over the repository's own registration, as owners resolve (owner_people).
    await app.state.organizations_repo.register("lab2", "gitlab")
    await app.state.db.execute(
        "INSERT INTO repo_preferences (repo, owners) VALUES ($1, $2) ON CONFLICT (repo) DO UPDATE SET owners = $2",
        "lab2/api",
        ["Theo"],
    )
    org = {
        (o["forge"], o["login"]) for o in (await _call(app, "GET", f"{PEOPLE}/suggestions", MASTER)).json()["owners"]
    }
    assert ("gitlab", "theo") in org and ("github", "theo") not in org, org


async def test_a_login_route_makes_no_person_and_says_when_a_cleared_login_falls_back(
    people_app: tuple[Any, Any],
) -> None:
    app, _keys = people_app
    await _user(app, "usr-fy", "fy@example.com")
    alone = await _call(app, "PUT", "/api/v1/users/usr-fy/github-login", MASTER, {"login": "fy"})
    assert alone.status_code == 200 and (await _call(app, "GET", PEOPLE, MASTER)).json()["count"] == 0, (
        "a user with no person gets the login alone"
    )
    await _user(app, "usr-gu", "gu@example.com", "gu-a")
    made = await _call(
        app,
        "POST",
        PEOPLE,
        MASTER,
        {
            "name": "Gu",
            "user_id": "usr-gu",
            "identities": [{"kind": "github", "value": "gu-a"}, {"kind": "github", "value": "gu-b"}],
        },
    )
    assert made.status_code == 201, made.text
    cleared = await _call(app, "PUT", "/api/v1/users/usr-gu/github-login", MASTER, {"login": ""})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["github_login"] == cleared.json()["fell_back_to"] == "gu-b", cleared.json()
    assert "gu-b" in cleared.json()["message"] and "oldest" in cleared.json()["message"]
    taken = await _call(app, "PUT", "/api/v1/users/usr-fy/github-login", MASTER, {"login": "GU-B"})
    assert taken.status_code == 409 and "someone else's" in taken.json()["detail"], (
        f"another person's identity was made a user's login: {taken.text}"
    )
    nobody = await _call(
        app, "POST", PEOPLE, MASTER, {"name": "Hz", "identities": [{"kind": "github", "value": "hz-login"}]}
    )
    assert nobody.status_code == 201, nobody.text
    held = await _call(app, "PUT", "/api/v1/users/usr-fy/github-login", MASTER, {"login": "HZ-login"})
    assert held.status_code == 409, (
        f"the identity of a person who signs in as nobody was made another user's login: {held.text}"
    )
