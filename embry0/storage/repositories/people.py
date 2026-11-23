"""People and their identities (migration 154, #179, spec 2026-11-23 section 3).

Reads and writes ``people`` and ``person_identities``. Every write runs in
one transaction with its ``audit_log`` row (``person.created``,
``person.updated``, ``person.deleted``, ``person.identity.added``,
``person.identity.removed``): who wrote it, and what changed. A write whose
audit row cannot be stored does not happen. Values are checked by
:mod:`embry0.services.people` before they are stored.

**A linked user's GitHub login, for one release.** ``users.github_login`` is
still what owners are matched by. While a person is linked to a user, that
login is one of the person's ``github`` identities: the one it names when it
still is one, else the person's oldest, else none. A change of the person's
GitHub identities, or of their link, writes it in the same transaction, and
:meth:`embry0.storage.repositories.users.UsersRepository.set_github_login`
writes the identity it replaces (:func:`follow_user_login`). Unlinking or
deleting a person leaves the user's login as it was.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg

from embry0.audit.actions import AuditAction
from embry0.services.people import PersonInvalid, check_fields, check_identity
from embry0.storage.database import DatabasePool

_COLUMNS = "id, name, email, user_id, timezone, hours, weekdays, disabled, created_by, created_at, updated_at"


@dataclass(frozen=True, slots=True)
class Identity:
    kind: str
    value: str


@dataclass(frozen=True, slots=True)
class Person:
    id: str
    name: str
    email: str | None
    user_id: str | None
    timezone: str | None
    hours: str | None
    weekdays: str | None
    disabled: bool
    created_by: str
    created_at: datetime | None
    updated_at: datetime | None
    identities: tuple[Identity, ...] = field(default=())


class PersonConflict(Exception):
    """A value another person, or another user, holds already: ``field`` names which."""

    def __init__(self, field: str) -> None:
        super().__init__(f"{field} is someone else's already")
        self.field = field


#: The unique index each conflict comes from, and the field it is about.
_CONFLICTS = {
    "uq_people_email_lower": "email",
    "uq_people_user": "user_id",
    "uq_person_identities_kind_value": "identity",
    "uq_users_github_login_lower": "github login",
}


def _conflict(exc: asyncpg.UniqueViolationError) -> PersonConflict:
    return PersonConflict(_CONFLICTS.get(str(getattr(exc, "constraint_name", "") or ""), "a value"))


_NO_USER = PersonInvalid("user_id", "no such user")


async def _audit(conn: Any, action: AuditAction, *, actor: str, details: dict[str, Any]) -> None:
    """The write's audit row, on its connection: it commits with the write or not at all."""
    await conn.execute(
        "INSERT INTO audit_log (action, actor, details) VALUES ($1, $2, $3)", action, actor or "system", details
    )


def _identities(rows: Iterable[Any]) -> dict[str, tuple[Identity, ...]]:
    out: dict[str, list[Identity]] = {}
    for row in rows:
        out.setdefault(row["person_id"], []).append(Identity(row["kind"], row["value"]))
    return {person: tuple(found) for person, found in out.items()}


def _person(row: Any, identities: tuple[Identity, ...]) -> Person:
    return Person(**{name: row[name] for name in _COLUMNS.split(", ")}, identities=identities)


async def _read(conn: Any, where: str, *args: Any) -> list[Person]:
    rows = await conn.fetch(f"SELECT {_COLUMNS} FROM people WHERE {where} ORDER BY LOWER(name), id", *args)
    if not rows:
        return []
    found = _identities(
        await conn.fetch(
            "SELECT person_id, kind, value FROM person_identities WHERE person_id = ANY($1::text[]) "
            "ORDER BY created_at, kind, LOWER(value)",
            [row["id"] for row in rows],
        )
    )
    return [_person(row, found.get(row["id"], ())) for row in rows]


async def _one(conn: Any, person_id: str) -> Person | None:
    people = await _read(conn, "id = $1", person_id)
    return people[0] if people else None


async def sync_user_login(conn: Any, person_id: str) -> None:
    """Keep the GitHub login of the user ``person_id`` is linked to one of the person's ``github`` identities: the
    one it names while it still is one, else the oldest, else none. Nothing for a person linked to no user."""
    user_id = await conn.fetchval("SELECT user_id FROM people WHERE id = $1", person_id)
    if not user_id:
        return
    current = await conn.fetchval("SELECT github_login FROM users WHERE id = $1", user_id)
    logins = [
        row["value"]
        for row in await conn.fetch(
            "SELECT value FROM person_identities WHERE person_id = $1 AND kind = 'github' ORDER BY created_at, value",
            person_id,
        )
    ]
    if current and current.lower() in {login.lower() for login in logins}:
        return
    await conn.execute("UPDATE users SET github_login = $2 WHERE id = $1", user_id, logins[0] if logins else None)


async def follow_user_login(conn: Any, user_id: str, old: str | None, new: str | None, *, actor: str) -> None:
    """A user's GitHub login changed from ``old`` to ``new``, on ``conn`` in the change's transaction: the person
    linked to the user, if any, gets ``new`` as a ``github`` identity in place of ``old`` (#179, for one release).
    A user linked to no person is left so: embry0 makes a person only when someone adds one."""
    person_id = await conn.fetchval("SELECT id FROM people WHERE user_id = $1", user_id)
    if not person_id or (old or "").lower() == (new or "").lower():
        return
    if old:
        removed = await conn.fetchval(
            "DELETE FROM person_identities WHERE person_id = $1 AND kind = 'github' AND LOWER(value) = LOWER($2) "
            "RETURNING value",
            person_id,
            old,
        )
        if removed:
            await _audit(
                conn,
                "person.identity.removed",
                actor=actor,
                details={"person_id": person_id, "kind": "github", "value": removed, "source": "users.github_login"},
            )
    if new:
        _, login = check_identity("github", new)
        holder = await conn.fetchval(
            "SELECT person_id FROM person_identities WHERE kind = 'github' AND LOWER(value) = LOWER($1)", login
        )
        if holder == person_id:
            return  # the person's already: one of their other GitHub logins
        # Another person's raises the unique violation, as another user's does: the login is someone else's.
        await conn.execute(
            "INSERT INTO person_identities (person_id, kind, value) VALUES ($1, 'github', $2)", person_id, login
        )
        await _audit(
            conn,
            "person.identity.added",
            actor=actor,
            details={"person_id": person_id, "kind": "github", "value": login, "source": "users.github_login"},
        )


class PeopleRepository:
    """Reads and writes ``people`` and ``person_identities``."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self, person_id: str) -> Person | None:
        async with self._db.transaction() as conn:
            return await _one(conn, person_id)

    async def all(self, *, include_disabled: bool = False) -> list[Person]:
        """Every person by name; the disabled too with ``include_disabled``."""
        async with self._db.transaction() as conn:
            return await _read(conn, "TRUE" if include_disabled else "NOT disabled")

    async def by_user(self, user_id: str) -> Person | None:
        async with self._db.transaction() as conn:
            found = await _read(conn, "user_id = $1", user_id)
        return found[0] if found else None

    async def by_email(self, email: str) -> Person | None:
        async with self._db.transaction() as conn:
            found = await _read(conn, "LOWER(email) = LOWER($1)", email.strip())
        return found[0] if found else None

    async def by_identity(self, kind: str, value: str) -> Person | None:
        """The person whose identity ``kind`` is ``value``, compared as stored: without regard to case."""
        kind, value = check_identity(kind, value)
        async with self._db.transaction() as conn:
            found = await _read(
                conn,
                "id = (SELECT person_id FROM person_identities WHERE kind = $1 AND LOWER(value) = LOWER($2))",
                kind,
                value,
            )
        return found[0] if found else None

    async def create(
        self, fields: Mapping[str, Any], *, identities: Iterable[tuple[str, str]] = (), actor: str
    ) -> Person:
        """A new person with ``fields`` (``name`` at least) and ``identities``, audited as one ``person.created``."""
        values = check_fields(fields)
        if "name" not in values:
            raise PersonInvalid("name", "give a name")
        checked = [check_identity(kind, value) for kind, value in identities]
        person_id = f"per-{uuid.uuid4().hex[:12]}"
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    "INSERT INTO people (id, name, email, user_id, timezone, hours, weekdays, disabled, created_by) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                    person_id,
                    values["name"],
                    values.get("email"),
                    values.get("user_id"),
                    values.get("timezone"),
                    values.get("hours"),
                    values.get("weekdays"),
                    bool(values.get("disabled", False)),
                    actor or "system",
                )
                for kind, value in checked:
                    await conn.execute(
                        "INSERT INTO person_identities (person_id, kind, value) VALUES ($1, $2, $3)",
                        person_id,
                        kind,
                        value,
                    )
                await sync_user_login(conn, person_id)
                await _audit(
                    conn,
                    "person.created",
                    actor=actor,
                    details={
                        "person_id": person_id,
                        "fields": {key: value for key, value in values.items() if value is not None},
                        "identities": [{"kind": kind, "value": value} for kind, value in checked],
                    },
                )
                person = await _one(conn, person_id)
        except asyncpg.UniqueViolationError as exc:
            raise _conflict(exc) from None
        except asyncpg.ForeignKeyViolationError:
            raise _NO_USER from None
        assert person is not None
        return person

    async def update(self, person_id: str, fields: Mapping[str, Any], *, actor: str) -> Person | None:
        """``fields`` written on the person (``None`` clears an optional one), audited as ``person.updated`` with
        each field's value before and after. None when there is no such person; a write that changes nothing writes
        no audit row."""
        values = check_fields(fields)
        try:
            async with self._db.transaction() as conn:
                before = await conn.fetchrow(f"SELECT {_COLUMNS} FROM people WHERE id = $1 FOR UPDATE", person_id)
                if before is None:
                    return None
                changed = {key: value for key, value in values.items() if before[key] != value}
                if changed:
                    sets = ", ".join(f"{key} = ${i}" for i, key in enumerate(changed, start=2))
                    await conn.execute(
                        f"UPDATE people SET {sets}, updated_at = now() WHERE id = $1", person_id, *changed.values()
                    )
                    if "user_id" in changed:
                        await sync_user_login(conn, person_id)
                    await _audit(
                        conn,
                        "person.updated",
                        actor=actor,
                        details={
                            "person_id": person_id,
                            "changed": {key: [before[key], value] for key, value in changed.items()},
                        },
                    )
                return await _one(conn, person_id)
        except asyncpg.UniqueViolationError as exc:
            raise _conflict(exc) from None
        except asyncpg.ForeignKeyViolationError:
            raise _NO_USER from None

    async def delete(self, person_id: str, *, actor: str) -> bool:
        """Delete the person and their identities, audited as ``person.deleted``. False when there is none. A user
        they were linked to keeps their GitHub login."""
        async with self._db.transaction() as conn:
            before = await _one(conn, person_id)
            if before is None:
                return False
            await conn.execute("DELETE FROM people WHERE id = $1", person_id)
            await _audit(
                conn,
                "person.deleted",
                actor=actor,
                details={
                    "person_id": person_id,
                    "name": before.name,
                    "user_id": before.user_id,
                    "identities": [{"kind": i.kind, "value": i.value} for i in before.identities],
                },
            )
        return True

    async def add_identity(self, person_id: str, kind: str, value: str, *, actor: str) -> Person | None:
        """Give the person an identity, audited as ``person.identity.added``; None when there is no such person.
        :class:`PersonConflict` when another person holds it."""
        kind, value = check_identity(kind, value)
        try:
            async with self._db.transaction() as conn:
                if await conn.fetchval("SELECT 1 FROM people WHERE id = $1 FOR UPDATE", person_id) is None:
                    return None
                await conn.execute(
                    "INSERT INTO person_identities (person_id, kind, value) VALUES ($1, $2, $3)", person_id, kind, value
                )
                await conn.execute("UPDATE people SET updated_at = now() WHERE id = $1", person_id)
                if kind == "github":
                    await sync_user_login(conn, person_id)
                await _audit(
                    conn,
                    "person.identity.added",
                    actor=actor,
                    details={"person_id": person_id, "kind": kind, "value": value},
                )
                return await _one(conn, person_id)
        except asyncpg.UniqueViolationError as exc:
            raise _conflict(exc) from None

    async def remove_identity(self, person_id: str, kind: str, value: str, *, actor: str) -> Person | None:
        """Take an identity from the person, audited as ``person.identity.removed``; None when the person has no
        such identity."""
        kind, value = check_identity(kind, value)
        try:
            async with self._db.transaction() as conn:
                removed = await conn.fetchval(
                    "DELETE FROM person_identities WHERE person_id = $1 AND kind = $2 AND LOWER(value) = LOWER($3) "
                    "RETURNING value",
                    person_id,
                    kind,
                    value,
                )
                if removed is None:
                    return None
                await conn.execute("UPDATE people SET updated_at = now() WHERE id = $1", person_id)
                if kind == "github":
                    await sync_user_login(conn, person_id)
                await _audit(
                    conn,
                    "person.identity.removed",
                    actor=actor,
                    details={"person_id": person_id, "kind": kind, "value": removed},
                )
                return await _one(conn, person_id)
        except asyncpg.UniqueViolationError as exc:
            raise _conflict(exc) from None
