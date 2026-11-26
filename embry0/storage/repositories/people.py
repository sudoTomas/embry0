"""People and their identities (migration 154, #179, spec 2026-11-23 section 3).

Reads and writes ``people`` and ``person_identities``. Every write runs in
one transaction with its ``audit_log`` row (``person.created``,
``person.updated``, ``person.deleted``, ``person.identity.added``,
``person.identity.removed``): who wrote it, and what changed. A write whose
audit row cannot be stored does not happen. Values are checked by
:mod:`embry0.services.people` before they are stored.

Owners are matched by the people's identities alone
(:mod:`embry0.services.owner_people`). The user's GitHub login that stood in
for them, ``users.github_login``, and the sync that kept it one of a linked
person's identities, were retired with migration 162 (#179).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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
    """A value another person holds already: ``field`` names which."""

    def __init__(self, field: str) -> None:
        super().__init__(f"{field} is someone else's already")
        self.field = field


#: The unique index each conflict comes from, and the field it is about.
_CONFLICTS = {
    "uq_people_email_lower": "email",
    "uq_people_user": "user_id",
    "uq_person_identities_kind_value": "identity",
}


def _conflict(exc: asyncpg.UniqueViolationError) -> PersonConflict:
    return PersonConflict(_CONFLICTS.get(str(getattr(exc, "constraint_name", "") or ""), "a value"))


_NO_USER = PersonInvalid("user_id", "no such user")


class PersonChanged(Exception):
    """The person changed since the version a write expected: ``updated_at`` is the one stored now."""

    def __init__(self, updated_at: datetime) -> None:
        super().__init__(f"the person changed at {updated_at.isoformat()}")
        self.updated_at = updated_at


#: The audit events a transaction wrote, to append to the audit file once it commits.
Written = list[tuple[AuditAction, str, dict[str, Any]]]


async def _audit(conn: Any, action: AuditAction, *, actor: str, details: dict[str, Any], into: Written) -> None:
    """The write's audit row, on its connection: it commits with the write or not at all. The event is kept in
    ``into`` for the audit file, written once the transaction commits (:func:`append_audit_file`)."""
    await conn.execute(
        "INSERT INTO audit_log (action, actor, details) VALUES ($1, $2, $3)", action, actor or "system", details
    )
    into.append((action, actor or "system", details))


def append_audit_file(written: Written, audit_log_path: str | Path | None) -> None:
    """The events a committed transaction wrote, appended to the audit file (and logged), as ``emit_audit`` does
    for every other audited write."""
    for action, actor, details in written:
        _append_one(action, actor, details, audit_log_path)


def _append_one(action: AuditAction, actor: str, details: dict[str, Any], audit_log_path: str | Path | None) -> None:
    from embry0.audit.logger import emit_audit_event

    emit_audit_event(action, actor=actor, details=details, audit_log_path=audit_log_path)


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


class PeopleRepository:
    """Reads and writes ``people`` and ``person_identities``."""

    def __init__(self, db: DatabasePool, *, audit_log_path: str | Path | None = None) -> None:
        self._db = db
        self._audit_log_path = audit_log_path

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
        """The person whose identity ``kind`` is ``value``, compared as stored: without regard to case. None for a value
        no identity of the kind can be, such as a login GitHub would not take: nobody holds it."""
        try:
            kind, value = check_identity(kind, value)
        except PersonInvalid:
            return None
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
        written: Written = []
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
                await _audit(
                    conn,
                    "person.created",
                    actor=actor,
                    details={
                        "person_id": person_id,
                        "fields": {key: value for key, value in values.items() if value is not None},
                        "identities": [{"kind": kind, "value": value} for kind, value in checked],
                    },
                    into=written,
                )
                person = await _one(conn, person_id)
        except asyncpg.UniqueViolationError as exc:
            raise _conflict(exc) from None
        except asyncpg.ForeignKeyViolationError:
            raise _NO_USER from None
        append_audit_file(written, self._audit_log_path)
        assert person is not None
        return person

    async def update(
        self,
        person_id: str,
        fields: Mapping[str, Any],
        *,
        actor: str,
        identities: Iterable[tuple[str, str]] | None = None,
        expect_updated_at: datetime | None = None,
    ) -> Person | None:
        """``fields`` written on the person (``None`` clears an optional one), audited as ``person.updated`` with
        each field's value before and after; and with ``identities``, the person's identities made exactly those,
        each one taken or given audited as ``person.identity.removed`` or ``person.identity.added``. None when there
        is no such person; :class:`PersonChanged` when ``expect_updated_at`` is not the version stored. A write that
        changes nothing writes no audit row."""
        values = check_fields(fields)
        wanted = None if identities is None else [check_identity(kind, value) for kind, value in identities]
        written: Written = []
        try:
            async with self._db.transaction() as conn:
                before = await conn.fetchrow(f"SELECT {_COLUMNS} FROM people WHERE id = $1 FOR UPDATE", person_id)
                if before is None:
                    return None
                if expect_updated_at is not None and before["updated_at"] != expect_updated_at:
                    raise PersonChanged(before["updated_at"])
                changed = {key: value for key, value in values.items() if before[key] != value}
                if changed:
                    sets = ", ".join(f"{key} = ${i}" for i, key in enumerate(changed, start=2))
                    await conn.execute(
                        f"UPDATE people SET {sets}, updated_at = now() WHERE id = $1", person_id, *changed.values()
                    )
                    await _audit(
                        conn,
                        "person.updated",
                        actor=actor,
                        details={
                            "person_id": person_id,
                            "changed": {key: [before[key], value] for key, value in changed.items()},
                        },
                        into=written,
                    )
                await self._replace_identities(conn, person_id, wanted, actor=actor, into=written)
                person = await _one(conn, person_id)
        except asyncpg.UniqueViolationError as exc:
            raise _conflict(exc) from None
        except asyncpg.ForeignKeyViolationError:
            raise _NO_USER from None
        append_audit_file(written, self._audit_log_path)
        return person

    @staticmethod
    async def _replace_identities(
        conn: Any,
        person_id: str,
        wanted: list[tuple[str, str]] | None,
        *,
        actor: str,
        into: Written,
    ) -> None:
        """Make the person's identities exactly ``wanted`` (nothing when None)."""
        if wanted is None:
            return
        rows = await conn.fetch("SELECT kind, value FROM person_identities WHERE person_id = $1", person_id)
        held = {(row["kind"], row["value"].lower()): row["value"] for row in rows}
        want = {(kind, value.lower()): value for kind, value in wanted}
        for key in sorted(set(held) - set(want)):
            await conn.execute(
                "DELETE FROM person_identities WHERE person_id = $1 AND kind = $2 AND LOWER(value) = $3",
                person_id,
                key[0],
                key[1],
            )
            await _audit(
                conn,
                "person.identity.removed",
                actor=actor,
                details={"person_id": person_id, "kind": key[0], "value": held[key]},
                into=into,
            )
        for key in [key for key in want if key not in held]:
            kind, value = key[0], want[key]
            await conn.execute(
                "INSERT INTO person_identities (person_id, kind, value) VALUES ($1, $2, $3)", person_id, kind, value
            )
            await _audit(
                conn,
                "person.identity.added",
                actor=actor,
                details={"person_id": person_id, "kind": kind, "value": value},
                into=into,
            )
        if set(held) != set(want):
            await conn.execute("UPDATE people SET updated_at = now() WHERE id = $1", person_id)

    async def delete(self, person_id: str, *, actor: str) -> bool:
        """Delete the person and their identities, audited as ``person.deleted``. False when there is none."""
        written: Written = []
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
                into=written,
            )
        append_audit_file(written, self._audit_log_path)
        return True

    async def add_identity(self, person_id: str, kind: str, value: str, *, actor: str) -> Person | None:
        """Give the person an identity, audited as ``person.identity.added``; None when there is no such person.
        :class:`PersonConflict` when another person holds it."""
        kind, value = check_identity(kind, value)
        written: Written = []
        try:
            async with self._db.transaction() as conn:
                holder = await conn.fetchrow("SELECT user_id FROM people WHERE id = $1 FOR UPDATE", person_id)
                if holder is None:
                    return None
                await conn.execute(
                    "INSERT INTO person_identities (person_id, kind, value) VALUES ($1, $2, $3)", person_id, kind, value
                )
                await conn.execute("UPDATE people SET updated_at = now() WHERE id = $1", person_id)
                await _audit(
                    conn,
                    "person.identity.added",
                    actor=actor,
                    details={"person_id": person_id, "kind": kind, "value": value},
                    into=written,
                )
                person = await _one(conn, person_id)
        except asyncpg.UniqueViolationError as exc:
            raise _conflict(exc) from None
        append_audit_file(written, self._audit_log_path)
        return person

    async def remove_identity(self, person_id: str, kind: str, value: str, *, actor: str) -> Person | None:
        """Take an identity from the person, audited as ``person.identity.removed``; None when the person has no
        such identity, as for a value no identity of the kind can be."""
        try:
            kind, value = check_identity(kind, value)
        except PersonInvalid:
            return None
        written: Written = []
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
                await _audit(
                    conn,
                    "person.identity.removed",
                    actor=actor,
                    details={"person_id": person_id, "kind": kind, "value": removed},
                    into=written,
                )
                person = await _one(conn, person_id)
        except asyncpg.UniqueViolationError as exc:
            raise _conflict(exc) from None
        append_audit_file(written, self._audit_log_path)
        return person

    async def suggestions(self) -> dict[str, list[dict[str, Any]]]:
        """Who could be added as a person (D6: embry0 adds nobody by itself): each user who signs in, not disabled,
        that no person is linked to (``users``); and each owner embry0 keeps (a repository's ``owners`` preference,
        an area's owners), as stored, with the repository that names it (``owners``, ``{repo, owner}`` each, in no
        order: their reader sorts what it answers). Which
        forge an owner is a login on, and whether a person holds it, is the people's directory's to say
        (:func:`embry0.services.owner_people.unheld_owners`). A ``CODEOWNERS`` owner is read from the forge and is
        not listed."""
        async with self._db.transaction() as conn:
            users = await conn.fetch(
                "SELECT u.id, u.email, u.name FROM users u "
                "WHERE NOT u.disabled AND NOT EXISTS (SELECT 1 FROM people p WHERE p.user_id = u.id) "
                "ORDER BY LOWER(COALESCE(NULLIF(u.name, ''), u.email)), u.id"
            )
            owners = await conn.fetch(
                """
                SELECT DISTINCT o.repo, o.login
                  FROM (
                      SELECT rp.repo, jsonb_array_elements_text(rp.owners) AS login
                        FROM repo_preferences rp WHERE jsonb_typeof(rp.owners) = 'array'
                      UNION ALL
                      SELECT ra.repo, jsonb_array_elements_text(a -> 'owners') AS login
                        FROM repo_areas ra, jsonb_array_elements(
                            CASE WHEN jsonb_typeof(ra.data -> 'areas') = 'array' THEN ra.data -> 'areas' ELSE '[]' END
                        ) a
                       WHERE jsonb_typeof(a -> 'owners') = 'array'
                  ) o
                 WHERE o.login <> ''
                """
            )
        return {
            "users": [{"user_id": r["id"], "email": r["email"], "name": r["name"]} for r in users],
            "owners": [{"repo": r["repo"], "owner": r["login"]} for r in owners],
        }
