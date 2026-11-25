"""People and their identities on Postgres (#179, migration 154, ``PeopleRepository``).

Every write is audited in its own transaction, a value another person holds
is refused naming its field, and while a person is linked to a user the
user's ``github_login`` stays one of the person's ``github`` identities, both
ways (for one release).
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.users import UsersRepository

try:
    from embry0.services.people import PersonInvalid
    from embry0.storage.repositories import people as people_module
    from embry0.storage.repositories.people import PeopleRepository, PersonConflict
except ImportError:
    pytest.fail("there is no people repository", pytrace=False)

pytestmark = pytest.mark.requires_postgres


async def _user(db: DatabasePool, email: str, login: str | None = None) -> str:
    user = await UsersRepository(db).upsert_login(auth0_sub=f"sub|{email}", email=email, name="", tier="none")
    if login is not None:
        await db.execute("UPDATE users SET github_login = $2 WHERE id = $1", user.id, login)
    return user.id


async def _audits(db: DatabasePool) -> list[tuple[str, str, dict[str, Any]]]:
    rows = await db.fetch("SELECT action, actor, details FROM audit_log WHERE action LIKE 'person.%' ORDER BY id")
    return [(row["action"], row["actor"], row["details"]) for row in rows]


async def _login(db: DatabasePool, user_id: str) -> str | None:
    login: str | None = await db.fetchval("SELECT github_login FROM users WHERE id = $1", user_id)
    return login


async def test_a_person_is_written_read_and_found_by_any_case(db_with_migrations: DatabasePool) -> None:
    people = PeopleRepository(db_with_migrations)
    ada = await people.create(
        {"name": " Ada ", "email": "Ada@Example.com", "hours": "9-17", "weekdays": "mon-thu", "timezone": "UTC"},
        identities=[("github", "@Ada-L"), ("slack", "U024BE7LH")],
        actor="admin@example.com",
    )
    assert (ada.name, ada.email, ada.hours, ada.weekdays, ada.timezone, ada.disabled) == (
        "Ada",
        "Ada@Example.com",
        "09-17",
        "mon-thu",
        "UTC",
        False,
    )
    assert [(i.kind, i.value) for i in ada.identities] == [("github", "Ada-L"), ("slack", "U024BE7LH")]
    assert (await people.by_identity("github", "ada-l")) == ada, "an identity is found whatever its case"
    assert (await people.by_email("ADA@example.COM")) == ada, "an email is found whatever its case"
    assert await people.get(ada.id) == ada and await people.by_identity("gitlab", "ada-l") is None
    assert await people.by_identity("github", "ada-l-") is None, "a login no person can hold is nobody's, not an error"
    assert await people.remove_identity(ada.id, "github", "ada--l", actor="t") is None
    assert await _audits(db_with_migrations) == [
        (
            "person.created",
            "admin@example.com",
            {
                "person_id": ada.id,
                "fields": {
                    "name": "Ada",
                    "email": "Ada@Example.com",
                    "hours": "09-17",
                    "weekdays": "mon-thu",
                    "timezone": "UTC",
                },
                "identities": [{"kind": "github", "value": "Ada-L"}, {"kind": "slack", "value": "U024BE7LH"}],
            },
        )
    ], "a person's creation is one audit row, naming who wrote it and what"


async def test_a_value_another_person_holds_is_refused_naming_its_field(db_with_migrations: DatabasePool) -> None:
    people = PeopleRepository(db_with_migrations)
    user = await _user(db_with_migrations, "ada@example.com")
    ada = await people.create({"name": "Ada", "email": "ada@example.com", "user_id": user}, actor="t")
    await people.add_identity(ada.id, "github", "ada-l", actor="t")
    bo = await people.create({"name": "Bo"}, actor="t")
    for call, field in (
        (people.create({"name": "Ada again", "email": "ADA@example.com"}, actor="t"), "email"),
        (people.update(bo.id, {"user_id": user}, actor="t"), "user_id"),
        (people.add_identity(bo.id, "github", "ADA-L", actor="t"), "identity"),
    ):
        with pytest.raises(PersonConflict) as conflict:
            await call
        assert conflict.value.field == field, conflict.value
    with pytest.raises(PersonInvalid) as invalid:
        await people.update(bo.id, {"user_id": "usr-nobody"}, actor="t")
    assert invalid.value.field == "user_id"
    with pytest.raises(PersonInvalid) as nobody:
        await people.create({"name": "Cy", "user_id": "usr-nobody"}, actor="t")
    assert nobody.value.field == "user_id", "a person linked to a user that does not exist is refused naming user_id"
    assert await people.get(bo.id) == bo, "a refused write changed the person"


async def test_an_update_audits_each_change_and_a_write_that_changes_nothing_audits_none(
    db_with_migrations: DatabasePool,
) -> None:
    people = PeopleRepository(db_with_migrations)
    ada = await people.create({"name": "Ada", "hours": "09-17"}, actor="t")
    changed = await people.update(ada.id, {"name": "Ada L", "hours": None, "disabled": True}, actor="alex")
    assert changed is not None and (changed.name, changed.hours, changed.disabled) == ("Ada L", None, True)
    same = await people.update(ada.id, {"name": "Ada L"}, actor="alex")
    assert same == changed
    assert [(a, actor, d.get("changed")) for a, actor, d in await _audits(db_with_migrations)][1:] == [
        ("person.updated", "alex", {"name": ["Ada", "Ada L"], "hours": ["09-17", None], "disabled": [False, True]})
    ], "an update is audited with each field before and after, and a write that changes nothing is not"
    assert await people.all() == [] and await people.all(include_disabled=True) == [changed]
    assert await people.update("per-nobody", {"name": "X"}, actor="t") is None


async def test_a_write_whose_audit_row_cannot_be_stored_does_not_happen(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    people = PeopleRepository(db_with_migrations)
    ada = await people.create({"name": "Ada"}, actor="t")

    async def fails(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the audit log is full")

    monkeypatch.setattr(people_module, "_audit", fails)
    with pytest.raises(RuntimeError):
        await people.add_identity(ada.id, "github", "ada-l", actor="t")
    with pytest.raises(RuntimeError):
        await people.update(ada.id, {"name": "Ada L"}, actor="t")
    assert await people.get(ada.id) == ada, "a write was stored without its audit row"


async def test_deleting_a_person_takes_their_identities_and_leaves_their_users_login(
    db_with_migrations: DatabasePool,
) -> None:
    people = PeopleRepository(db_with_migrations)
    user = await _user(db_with_migrations, "ada@example.com")
    ada = await people.create({"name": "Ada", "user_id": user}, identities=[("github", "ada-l")], actor="t")
    assert await people.delete(ada.id, actor="alex") is True
    assert await people.by_identity("github", "ada-l") is None and await people.get(ada.id) is None
    assert await _login(db_with_migrations, user) == "ada-l", "the user keeps the login owners match them by"
    assert (await _audits(db_with_migrations))[-1][:2] == ("person.deleted", "alex")
    assert await people.delete(ada.id, actor="alex") is False


async def test_a_linked_users_login_follows_the_persons_github_identities(db_with_migrations: DatabasePool) -> None:
    people = PeopleRepository(db_with_migrations)
    user = await _user(db_with_migrations, "ada@example.com")
    ada = await people.create({"name": "Ada"}, identities=[("github", "ada-old")], actor="t")
    assert await _login(db_with_migrations, user) is None, "a person linked to no user writes no login"
    await people.update(ada.id, {"user_id": user}, actor="t")
    assert await _login(db_with_migrations, user) == "ada-old", "linking gives the user the person's login"
    await people.add_identity(ada.id, "github", "ada-new", actor="t")
    assert await _login(db_with_migrations, user) == "ada-old", "a second login leaves the one the user has"
    await people.remove_identity(ada.id, "github", "ADA-OLD", actor="t")
    assert await _login(db_with_migrations, user) == "ada-new", "the login the user had went: the next one"
    await people.add_identity(ada.id, "gitlab", "ada", actor="t")
    await people.remove_identity(ada.id, "github", "ada-new", actor="t")
    assert await _login(db_with_migrations, user) is None, "no github identity left: no login"
    assert await people.remove_identity(ada.id, "github", "ada-new", actor="t") is None


async def test_setting_a_users_login_moves_their_persons_identity(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    people, users = PeopleRepository(db), UsersRepository(db)
    user = await _user(db, "ada@example.com")
    ada = await people.create({"name": "Ada", "user_id": user}, identities=[("github", "ada-old")], actor="t")
    await users.set_github_login(user, "ada-new", actor="alex")
    moved = await people.get(ada.id)
    assert moved is not None and [(i.kind, i.value) for i in moved.identities] == [("github", "ada-new")], (
        f"the person's github identity must follow the user's login: {moved}"
    )
    assert [(a, actor) for a, actor, _ in await _audits(db)][-2:] == [
        ("person.identity.removed", "alex"),
        ("person.identity.added", "alex"),
    ]
    bo = await people.create({"name": "Bo"}, identities=[("github", "bo")], actor="t")
    with pytest.raises(Exception, match="unique"):
        await users.set_github_login(user, "BO", actor="alex")
    assert await _login(db, user) == "ada-new" and await people.get(bo.id) == bo, "a refused login changed nothing"
    await users.set_github_login(user, None, actor="alex")
    cleared = await people.get(ada.id)
    assert cleared is not None and cleared.identities == (), "clearing the login takes the identity"
    loner = await _user(db, "cy@example.com")
    await users.set_github_login(loner, "cy", actor="alex")
    assert await people.by_identity("github", "cy") is None, "a user linked to no person is not made one"


async def test_a_login_the_person_holds_keeps_both_and_a_cleared_one_falls_to_the_oldest(
    db_with_migrations: DatabasePool,
) -> None:
    """#672 review: setting a user's login to another the person holds dropped the first; clearing it left the user
    no login while the person held another."""
    db = db_with_migrations
    people, users = PeopleRepository(db), UsersRepository(db)
    user = await _user(db, "ada@example.com")
    ada = await people.create(
        {"name": "Ada", "user_id": user}, identities=[("github", "ada-a"), ("github", "ada-b")], actor="t"
    )
    assert await _login(db, user) == "ada-a", "linked with two logins: the oldest"
    answered = await users.set_github_login(user, "ADA-B", actor="t")
    kept = await people.get(ada.id)
    assert kept is not None and [i.value for i in kept.identities] == ["ada-a", "ada-b"], (
        f"a login the person holds already must not cost them the other: {kept}"
    )
    assert answered is not None and answered.github_login == "ADA-B"
    await people.add_identity(ada.id, "github", "ada-c", actor="t")
    assert await _login(db, user) == "ADA-B", "the login the user names stays while it is the person's, oldest or not"
    cleared = await users.set_github_login(user, None, actor="t")
    after = await people.get(ada.id)
    assert after is not None and [i.value for i in after.identities] == ["ada-a", "ada-c"]
    assert cleared is not None and cleared.github_login == "ada-a" == await _login(db, user), (
        f"a cleared login falls to the person's oldest left, and the answer says so: {cleared}"
    )


async def test_every_write_is_appended_to_the_audit_file_once_it_commits(
    db_with_migrations: DatabasePool, tmp_path: Any
) -> None:
    """#672 review: the writes were audited in the database and never in the file emit_audit also writes."""
    import json

    path = tmp_path / "audit.jsonl"
    db = db_with_migrations
    try:
        people, users = PeopleRepository(db, audit_log_path=path), UsersRepository(db, audit_log_path=path)
    except TypeError:
        pytest.fail("the people and users repositories write no audit file")
    user = await _user(db, "ada@example.com")
    ada = await people.create({"name": "Ada", "user_id": user}, identities=[("github", "ada-a")], actor="alex")
    with pytest.raises(PersonConflict):
        await people.create({"name": "Bo"}, identities=[("github", "ADA-A")], actor="alex")
    await people.update(ada.id, {"name": "Ada L"}, actor="alex")
    await users.set_github_login(user, "ada-z", actor="alex")
    await people.delete(ada.id, actor="alex")
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(e["action"], e["actor"]) for e in lines] == [
        ("person.created", "alex"),
        ("person.updated", "alex"),
        ("person.identity.removed", "alex"),
        ("person.identity.added", "alex"),
        ("person.deleted", "alex"),
    ], f"each committed write, and nothing of a refused one, is in the file: {lines}"
    assert lines[0]["details"]["person_id"] == ada.id


async def test_the_login_routes_refuse_what_github_refuses_and_a_person_error_is_a_422(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#672 review: the routes took ``abc-`` and ``ab--cd``, which the person's identity check refused inside the
    transaction, a 500 for a linked user; one rule (``forge_logins``) refuses them both everywhere."""
    from tests.fixtures.repository_writes import MASTER, call, repository_writes_app

    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        linked = await _user(app.state.db, "ada@example.com")
        alone = await _user(app.state.db, "bo@example.com")
        await PeopleRepository(app.state.db).create({"name": "Ada", "user_id": linked}, actor="t")
        answers = {
            (who, login): (await call(app, "PUT", f"/api/v1/users/{user}/github-login", MASTER, {"login": login}))
            for who, user in (("linked", linked), ("alone", alone))
            for login in ("abc-", "ab--cd")
        }
        assert {key: r.status_code for key, r in answers.items()} == dict.fromkeys(answers, 422), {
            key: (r.status_code, r.text[:80]) for key, r in answers.items()
        }
        from embry0.services.people import PersonInvalid

        async def refuses(*args: Any, **kwargs: Any) -> Any:
            raise PersonInvalid("value", "not a github login")

        monkeypatch.setattr(app.state.users_repo, "set_github_login", refuses)
        refused = await call(app, "PUT", f"/api/v1/users/{linked}/github-login", MASTER, {"login": "ada"})
        assert refused.status_code == 422, f"a person's refusal is the caller's 422, not a 500: {refused.text}"
