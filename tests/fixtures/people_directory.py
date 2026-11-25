"""People for the asks and decisions tests (#179): a directory of people who hold GitHub logins.

``directory({"ada": "ada@example.com"})`` is the people's directory with one
person, Ada, holding the GitHub login ``ada`` and reached at that email: what
the users' login-to-email map was before owners resolved to people. ``asked``
builds the ask loop's reader over one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

from embry0.storage.repositories.people import Identity, Person

if TYPE_CHECKING:
    from embry0.services.owner_people import Directory


def person(
    name: str,
    *,
    email: str | None = None,
    identities: Iterable[tuple[str, str]] = (),
    user_id: str | None = None,
    timezone: str | None = None,
    hours: str | None = None,
    weekdays: str | None = None,
    disabled: bool = False,
) -> Person:
    return Person(
        id=f"per-{name.lower()}",
        name=name,
        email=email,
        user_id=user_id,
        timezone=timezone,
        hours=hours,
        weekdays=weekdays,
        disabled=disabled,
        created_by="tests",
        created_at=None,
        updated_at=None,
        identities=tuple(Identity(kind=kind, value=value) for kind, value in identities),
    )


def directory(
    emails: Mapping[str, str] | None = None, *, people: Iterable[Person] = (), forges: Mapping[str, str] | None = None
) -> Directory:
    """A person per ``login: email`` of ``emails``, holding that GitHub login, and ``people`` besides."""
    from embry0.services.owner_people import Directory

    made = [person(login, email=email, identities=[("github", login)]) for login, email in (emails or {}).items()]
    return Directory(people=(*made, *people), forges=dict(forges or {}))


def asked(found: Directory) -> Callable[[Iterable[str | None]], Awaitable[Directory]]:
    """The ask loop's reader of the people (``AskLoop.people``), answering ``found`` every tick."""

    async def read(_repos: Iterable[str | None]) -> Directory:
        return found

    return read


def users_of(*rows: Any) -> list[Any]:
    """Rows as ``UsersRepository.people`` answers them."""
    return list(rows)
