"""The people API: read, add, change and remove people and their identities (#179, spec 2026-11-23 section 7).

    GET    /people                                    every person, the disabled too with ?include_disabled=true
    GET    /people/suggestions                        who could be added: users with no person, owner logins nobody holds
    GET    /people/{person_id}                        one person, with an ETag of the version read
    POST   /people                                    add a person, with their identities (admin)
    PATCH  /people/{person_id}                        change their fields, and with "identities" make those theirs
                                                      (admin; If-Match required)
    DELETE /people/{person_id}                        remove them and their identities (admin)
    POST   /people/{person_id}/identities             give them an identity (admin)
    DELETE /people/{person_id}/identities/{kind}/{value}  take one from them (admin)

Operators and admins read; admins write. People are the deployment's: a key
with a repository allowlist reads nobody and writes nothing
(``embry0.api.deployment_reads``). A write with problems is refused with one
422 naming every problem by where it is, never repeating a value
(``embry0.services.people.check_write``); a value another person or user
holds is a 409 naming the field. ``PATCH`` requires ``If-Match`` with the
``ETag`` a read answered (the person's ``updated_at``; 428 without it), so a
change someone made in between is refused (409) rather than overwritten,
identities added since included: ``embry0 people apply`` sends the version
each entry was exported with. Every write is audited (``person.*``) by the
repository.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, Response

from embry0.api.deployment_reads import deployment_hidden
from embry0.api.deps import request_actor, require_admin, require_operator
from embry0.api.repo_allowlist import deployment_writes
from embry0.services.people import PeopleInvalid, PersonInvalid, check_identity, check_write
from embry0.storage.repositories.people import PeopleRepository, Person, PersonChanged, PersonConflict

router = APIRouter()

_WRITES = [require_admin, deployment_writes("change the deployment's people")]


def _repo(request: Request) -> PeopleRepository:
    people = getattr(request.app.state, "people_repo", None)
    if people is None:
        raise HTTPException(status_code=503, detail="people storage not initialized")
    return people  # type: ignore[no-any-return]


def person_view(person: Person) -> dict[str, Any]:
    """A person as the API answers: who they are, never who added them (that is the audit log's)."""
    return {
        "id": person.id,
        "name": person.name,
        "email": person.email,
        "user_id": person.user_id,
        "timezone": person.timezone,
        "hours": person.hours,
        "weekdays": person.weekdays,
        "disabled": person.disabled,
        "identities": [{"kind": i.kind, "value": i.value} for i in person.identities],
        "updated_at": person.updated_at.isoformat() if person.updated_at else None,
    }


def _etag(person: Person) -> dict[str, str]:
    return {"ETag": f'"{person.updated_at.isoformat()}"'} if person.updated_at else {}


def _unprocessable(problems: list[tuple[tuple[str | int, ...], str]]) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=[{"loc": ["body", *loc], "type": "value_error", "msg": message} for loc, message in problems],
    )


def _conflict(exc: PersonConflict) -> HTTPException:
    return HTTPException(status_code=409, detail=f"that {exc.field} is someone else's already")


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="no such person")


def _if_match(value: str | None) -> datetime:
    """The version ``If-Match`` names. Without one, 428: a change made blind would overwrite whatever changed since,
    identities added since included. A value that names no version is a 412: it can match none."""
    if value is None or not value.strip():
        raise HTTPException(
            status_code=428,
            detail="a change names the version it was read at: send If-Match with the ETag a read of the person answered",
        )
    try:
        return datetime.fromisoformat(value.strip().strip('"'))
    except ValueError:
        raise HTTPException(status_code=412, detail="If-Match names no version of a person") from None


@router.get("/people", dependencies=[require_operator])
async def list_people(request: Request, include_disabled: bool = Query(default=False)) -> dict[str, Any]:
    """Every person by name. To a key with a repository allowlist, as a deployment with none: nobody."""
    if deployment_hidden(request):
        return {"people": [], "count": 0}
    people = [person_view(p) for p in await _repo(request).all(include_disabled=include_disabled)]
    return {"people": people, "count": len(people)}


@router.get("/people/suggestions", dependencies=[require_operator])
async def people_suggestions(request: Request) -> dict[str, Any]:
    """Who could be added as a person (embry0 adds nobody by itself, D6): each user who signs in with no person, and
    each owner login embry0 keeps that no person holds, on its repository's organization's forge (``{forge, login,
    repos}``). To a key with an allowlist: nobody."""
    from embry0.services.owner_people import directory_of, unheld_owners

    if deployment_hidden(request):
        return {"users": [], "owners": []}
    found = await _repo(request).suggestions()
    directory = await directory_of(request.app.state, (row["repo"] for row in found["owners"]))
    return {"users": found["users"], "owners": unheld_owners(found["owners"], directory)}


@router.get("/people/{person_id}", dependencies=[require_operator])
async def get_person(person_id: str, request: Request, response: Response) -> dict[str, Any]:
    if deployment_hidden(request):
        raise _not_found()
    person = await _repo(request).get(person_id)
    if person is None:
        raise _not_found()
    response.headers.update(_etag(person))
    return person_view(person)


@router.post("/people", status_code=201, dependencies=_WRITES)
async def add_person(request: Request, response: Response, payload: Any = Body(...)) -> dict[str, Any]:
    """Add a person: ``name`` and any other field, and ``identities``, a list of ``{kind, value}``."""
    fields, identities = _split(payload)
    try:
        values, checked = check_write(fields, identities, creating=True)
        person = await _repo(request).create(values, identities=checked or (), actor=request_actor(request))
    except PeopleInvalid as exc:
        raise _unprocessable(exc.problems) from None
    except PersonInvalid as exc:
        raise _unprocessable([((exc.field,), exc.problem)]) from None
    except PersonConflict as exc:
        raise _conflict(exc) from None
    response.headers.update(_etag(person))
    return person_view(person)


@router.patch("/people/{person_id}", dependencies=_WRITES)
async def change_person(
    person_id: str,
    request: Request,
    response: Response,
    payload: Any = Body(...),
    if_match: str | None = Header(default=None),
) -> dict[str, Any]:
    """Change the fields given (``null`` clears an optional one); with ``identities``, make them exactly the person's.
    ``If-Match``, the ``ETag`` a read answered, is required (428 without it), and a change someone made in between
    is refused (409)."""
    expected = _if_match(if_match)
    fields, identities = _split(payload)
    try:
        values, checked = check_write(fields, identities)
        person = await _repo(request).update(
            person_id, values, actor=request_actor(request), identities=checked, expect_updated_at=expected
        )
    except PeopleInvalid as exc:
        raise _unprocessable(exc.problems) from None
    except PersonInvalid as exc:
        raise _unprocessable([((exc.field,), exc.problem)]) from None
    except PersonConflict as exc:
        raise _conflict(exc) from None
    except PersonChanged as exc:
        raise HTTPException(
            status_code=409,
            detail=f"the person changed since it was read (now {exc.updated_at.isoformat()}): read it again",
        ) from None
    if person is None:
        raise _not_found()
    response.headers.update(_etag(person))
    return person_view(person)


@router.delete("/people/{person_id}", dependencies=_WRITES)
async def remove_person(person_id: str, request: Request) -> dict[str, Any]:
    """Remove the person and their identities. A user they were linked to keeps their GitHub login."""
    if not await _repo(request).delete(person_id, actor=request_actor(request)):
        raise _not_found()
    return {"deleted": person_id}


@router.post("/people/{person_id}/identities", dependencies=_WRITES)
async def add_identity(person_id: str, request: Request, payload: Any = Body(...)) -> dict[str, Any]:
    """Give the person an identity, ``{kind, value}``."""
    _, checked = _identity_write(payload)
    kind, value = checked[0]
    try:
        person = await _repo(request).add_identity(person_id, kind, value, actor=request_actor(request))
    except PersonConflict as exc:
        raise _conflict(exc) from None
    if person is None:
        raise _not_found()
    return person_view(person)


@router.delete("/people/{person_id}/identities/{kind}/{value}", dependencies=_WRITES)
async def remove_identity(person_id: str, kind: str, value: str, request: Request) -> dict[str, Any]:
    """Take an identity from the person; 404 when they have none such."""
    try:
        kind, value = check_identity(kind, value)
    except PersonInvalid:
        raise _not_found() from None
    person = await _repo(request).remove_identity(person_id, kind, value, actor=request_actor(request))
    if person is None:
        raise _not_found()
    return person_view(person)


def _split(payload: Any) -> tuple[Any, Any]:
    """A body's fields and its ``identities``, which :func:`check_write` judges apart; any other shape is its
    problem to name."""
    if not isinstance(payload, dict):
        raise _unprocessable([((), "a person is an object of fields")])
    fields = {key: value for key, value in payload.items() if key != "identities"}
    return fields, payload.get("identities")


def _identity_write(payload: Any) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    try:
        fields, checked = check_write({}, [payload])
    except PeopleInvalid as exc:
        # One identity: its problems are the body's own, not the first of a list.
        raise _unprocessable([(tuple(loc[2:]) or (), message) for loc, message in exc.problems]) from None
    return fields, checked or []
