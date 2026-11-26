"""People and their identities on Postgres (#179, migration 154, ``PeopleRepository``).

Every write is audited in its own transaction, and a value another person
holds is refused naming its field. A user's own GitHub login, which a linked
person's identities were kept in step with for one release, was retired with
migration 162.
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


async def _user(db: DatabasePool, email: str) -> str:
    user = await UsersRepository(db).upsert_login(auth0_sub=f"sub|{email}", email=email, name="", tier="none")
    return user.id


async def _audits(db: DatabasePool) -> list[tuple[str, str, dict[str, Any]]]:
    rows = await db.fetch("SELECT action, actor, details FROM audit_log WHERE action LIKE 'person.%' ORDER BY id")
    return [(row["action"], row["actor"], row["details"]) for row in rows]


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


async def test_deleting_a_person_takes_their_identities(
    db_with_migrations: DatabasePool,
) -> None:
    people = PeopleRepository(db_with_migrations)
    user = await _user(db_with_migrations, "ada@example.com")
    ada = await people.create({"name": "Ada", "user_id": user}, identities=[("github", "ada-l")], actor="t")
    assert await people.delete(ada.id, actor="alex") is True
    assert await people.by_identity("github", "ada-l") is None and await people.get(ada.id) is None
    assert (await _audits(db_with_migrations))[-1][:2] == ("person.deleted", "alex")
    assert await people.delete(ada.id, actor="alex") is False


async def test_every_write_is_appended_to_the_audit_file_once_it_commits(
    db_with_migrations: DatabasePool, tmp_path: Any
) -> None:
    """#672 review: the writes were audited in the database and never in the file emit_audit also writes."""
    import json

    path = tmp_path / "audit.jsonl"
    db = db_with_migrations
    try:
        people = PeopleRepository(db, audit_log_path=path)
    except TypeError:
        pytest.fail("the people repository writes no audit file")
    user = await _user(db, "ada@example.com")
    ada = await people.create({"name": "Ada", "user_id": user}, identities=[("github", "ada-a")], actor="alex")
    with pytest.raises(PersonConflict):
        await people.create({"name": "Bo"}, identities=[("github", "ADA-A")], actor="alex")
    await people.update(ada.id, {"name": "Ada L"}, actor="alex")
    await people.add_identity(ada.id, "github", "ada-z", actor="alex")
    await people.remove_identity(ada.id, "github", "ada-a", actor="alex")
    await people.delete(ada.id, actor="alex")
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(e["action"], e["actor"]) for e in lines] == [
        ("person.created", "alex"),
        ("person.updated", "alex"),
        ("person.identity.added", "alex"),
        ("person.identity.removed", "alex"),
        ("person.deleted", "alex"),
    ], f"each committed write, and nothing of a refused one, is in the file: {lines}"
    assert lines[0]["details"]["person_id"] == ada.id


async def test_two_people_given_one_login_at_once_are_one_holder(db_with_migrations: DatabasePool) -> None:
    """With no user's login to check beside it (#179), a GitHub login is one person's by its unique index alone: of
    two writes at once, one takes it and the other is refused naming the identity."""
    import asyncio

    people = PeopleRepository(db_with_migrations)
    bo = await people.create({"name": "Bo"}, actor="t")
    outcomes = await asyncio.gather(
        people.add_identity(bo.id, "github", "RACE-x", actor="t"),
        people.create({"name": "Cy"}, identities=[("github", "race-x")], actor="t"),
        return_exceptions=True,
    )
    refused = [o for o in outcomes if isinstance(o, Exception)]
    assert len(refused) == 1 and isinstance(refused[0], PersonConflict) and refused[0].field == "identity", outcomes
