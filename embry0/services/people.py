"""People and their identities: what a person's fields and an identity may hold (#179, spec 2026-11-23).

A person is one row of ``people``: a name, an optional email, an optional
link to the ``users`` row they sign in with, optional working hours, and a
``disabled`` flag. Their identities say who they are elsewhere: a login on a
forge (``github``, ``gitlab``) or a handle in a chat (``telegram``,
``slack``, ``teams``, ``discord``). An identity's value is one person's on
its kind, compared without regard to case: a forge login is unique on its
forge, and a chat's ids are case-insensitive or numeric.

This module only checks and normalises; :mod:`embry0.storage.repositories.people`
stores. Every check raises :class:`PersonInvalid` naming the field, and its
message never repeats the value.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any, Final, Literal, get_args

from embry0.forge_logins import GITHUB_LOGIN_RE, GITLAB_LOGIN_RE
from embry0.settings.sections.asks import check_timezone, parse_hours, parse_weekdays

ForgeKind = Literal["github", "gitlab"]
ChatKind = Literal["telegram", "slack", "teams", "discord"]
IdentityKind = Literal["github", "gitlab", "telegram", "slack", "teams", "discord"]
FORGE_KINDS: Final[tuple[str, ...]] = get_args(ForgeKind)
CHAT_KINDS: Final[tuple[str, ...]] = get_args(ChatKind)
IDENTITY_KINDS: Final[tuple[str, ...]] = get_args(IdentityKind)

MAX_NAME: Final = 200
#: The longest address RFC 5321 lets a mailbox have.
MAX_EMAIL: Final = 320
MAX_IDENTITY: Final = 255
#: The fields of a person a write may set; ``None`` clears an optional one.
FIELDS: Final = ("name", "email", "user_id", "timezone", "hours", "weekdays", "disabled")
OPTIONAL: Final = frozenset({"email", "user_id", "timezone", "hours", "weekdays"})

_EMAIL: Final = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class PersonInvalid(ValueError):
    """A value a person or an identity cannot hold; ``field`` names where it was given."""

    def __init__(self, field: str, problem: str) -> None:
        super().__init__(f"{field}: {problem}")
        self.field = field
        self.problem = problem


def _one_line(text: str) -> bool:
    return not any(unicodedata.category(char)[0] == "C" or char in "  " for char in text)


def check_name(value: Any) -> str:
    """A person's name as the dashboard and a chat say it: one line of text, blanks around it dropped."""
    if not isinstance(value, str) or not value.strip():
        raise PersonInvalid("name", "give a name")
    name = value.strip()
    if len(name) > MAX_NAME:
        raise PersonInvalid("name", f"a name is at most {MAX_NAME} characters")
    if not _one_line(name):
        raise PersonInvalid("name", "a name is one line of plain text")
    return name


def check_email(value: Any) -> str | None:
    """An email address, blanks around it dropped; None for none. Compared without regard to case when stored."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise PersonInvalid("email", "an email address is text")
    email = value.strip()
    if not email:
        return None
    if len(email) > MAX_EMAIL or not _EMAIL.fullmatch(email) or not _one_line(email):
        raise PersonInvalid("email", "not an email address")
    return email


def check_identity(kind: Any, value: Any) -> tuple[str, str]:
    """An identity as stored: its kind, and its value with blanks around it, and a forge login's ``@``, dropped."""
    if kind not in IDENTITY_KINDS:
        raise PersonInvalid("kind", f"an identity is one of {', '.join(IDENTITY_KINDS)}")
    if not isinstance(value, str) or not value.strip():
        raise PersonInvalid("value", f"give the {kind} identity")
    text = value.strip()
    if kind in FORGE_KINDS:
        text = text.removeprefix("@")
        grammar = GITHUB_LOGIN_RE if kind == "github" else GITLAB_LOGIN_RE
        if not grammar.fullmatch(text):
            raise PersonInvalid("value", f"not a {kind} login")
        return kind, text
    if len(text) > MAX_IDENTITY:
        raise PersonInvalid("value", f"a {kind} identity is at most {MAX_IDENTITY} characters")
    if any(char.isspace() for char in text) or not _one_line(text):
        raise PersonInvalid("value", f"a {kind} identity is one word of plain text")
    return kind, text


def check_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of a write, checked and normalised: hours, weekdays and the time zone by the ``asks`` section's
    grammar, so a value moves between a person and their organization unchanged. ``None`` clears an optional
    field. A field this module does not know is refused."""
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise PersonInvalid(unknown[0], "not a field of a person")
    out: dict[str, Any] = {}
    for field, value in fields.items():
        if field == "name":
            out[field] = check_name(value)
        elif field == "email":
            out[field] = check_email(value)
        elif field == "disabled":
            if not isinstance(value, bool):
                raise PersonInvalid(field, "true or false")
            out[field] = value
        elif value is None:
            out[field] = None
        elif not isinstance(value, str) or not value.strip():
            raise PersonInvalid(field, "give a value, or none to clear it")
        elif field == "user_id":
            out[field] = value.strip()
        else:
            out[field] = _working_time(field, value)
    return out


def _working_time(field: str, value: str) -> str:
    try:
        if field == "hours":
            start, end = parse_hours(value)
            return f"{start:02d}-{end:02d}"
        if field == "weekdays":
            parse_weekdays(value)
            return "".join(value.split()).lower()
        return check_timezone(value.strip())
    except ValueError as exc:
        raise PersonInvalid(field, str(exc)) from None
