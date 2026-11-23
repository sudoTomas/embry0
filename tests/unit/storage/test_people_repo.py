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


async def test_a_users_login_kept_is_the_one_it_names_and_a_login_of_the_persons_own_is_no_conflict(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    people, users = PeopleRepository(db), UsersRepository(db)
    user = await _user(db, "ada@example.com")
    ada = await people.create(
        {"name": "Ada", "user_id": user}, identities=[("github", "ada-a"), ("github", "ada-c")], actor="t"
    )
    assert await _login(db, user) == "ada-a", "linked with two logins: the oldest"
    await users.set_github_login(user, "ADA-C", actor="t")
    moved = await people.get(ada.id)
    assert moved is not None and [i.value for i in moved.identities] == ["ada-c"], (
        f"a login the person holds already is theirs, no conflict, and the one it replaced goes: {moved}"
    )
    await people.add_identity(ada.id, "github", "ada-b", actor="t")
    await people.add_identity(ada.id, "github", "ada-d", actor="t")
    await users.set_github_login(user, "ada-d", actor="t")
    await people.add_identity(ada.id, "github", "ada-e", actor="t")
    assert await _login(db, user) == "ada-d", "the login the user names stays while it is the person's, oldest or not"
