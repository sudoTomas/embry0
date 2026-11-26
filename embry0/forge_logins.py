"""The logins owners are named by: one rule for every place that names a repository's or an area's owners.

Repository owners (``repo_preferences.owners``), area owners (the areas
document, spec 2026-11-10), ``CODEOWNERS``, the people a chat assistant may
ask and a person's ``github`` and ``gitlab`` identities (``person_identities``,
#179) are all matched against
each other, so each forge's logins are checked with that forge's own pattern:
a login the forge would not let anyone sign up with can be nobody's.

An owner is written ``github:<login>`` or ``gitlab:<username>``, or bare,
``<login>``: a bare one is on the forge of the repository's organization
(#181), which is how every owner written before the forge was named reads. A
bare one is checked by GitHub's pattern, the one every owner was checked by
before: every login it takes GitLab takes too, so a bare owner names the same
login on either forge, and a GitLab username GitHub would not take (an
underscore, a dot) is written ``gitlab:<username>``.
:func:`parse_owner` checks an owner as written; :func:`split_owner` reads one
as stored, as its named forge (None: bare) and its login.
Leaf module: imports nothing of embry0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: A GitHub login: up to 39 letters, digits and single hyphens, not starting or ending with one.
GITHUB_LOGIN_RE: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
#: A GitLab username: letters, digits, underscores, dots and hyphens, starting with a letter, digit or underscore.
GITLAB_LOGIN_RE: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}$")

#: The forges an owner may be named on, each with its logins' pattern.
LOGIN_PATTERNS: Final = {"github": GITHUB_LOGIN_RE, "gitlab": GITLAB_LOGIN_RE}


@dataclass(frozen=True)
class OwnerName:
    """An owner as written: its ``forge`` (None: bare, the forge of the repository's organization) and login."""

    forge: str | None
    login: str

    def __str__(self) -> str:
        return self.login if self.forge is None else f"{self.forge}:{self.login}"


def parse_owner(text: str) -> OwnerName | None:
    """``text`` as an owner: ``github:<login>``, ``gitlab:<username>`` or a bare login, an ``@`` before the login
    dropped; None when it names no one. A named forge's login is checked by that forge's pattern; a bare one by
    GitHub's, which GitLab's takes in whole, so it is a login on whichever forge the organization is on."""
    raw = text.strip()
    named, colon, rest = raw.partition(":")
    if colon:
        on = named.strip().lower()
        login = rest.strip().removeprefix("@")
        pattern = LOGIN_PATTERNS.get(on)
        return OwnerName(on, login) if pattern is not None and pattern.fullmatch(login) else None
    login = raw.removeprefix("@")
    return OwnerName(None, login) if GITHUB_LOGIN_RE.fullmatch(login) else None


def split_owner(text: object) -> tuple[str | None, str]:
    """An owner as stored, read leniently: its named forge (None: bare, the organization's) and its login, without
    an ``@``, lower-cased. What resolving an owner reads (``owner_people``); writes check with :func:`parse_owner`."""
    raw = str(text).strip()
    named, colon, rest = raw.partition(":")
    if colon and named.strip().lower() in LOGIN_PATTERNS:
        return named.strip().lower(), rest.strip().lstrip("@").lower()
    return None, raw.lstrip("@").lower()


def owner_text(text: str) -> str | None:
    """An owner as it is stored: ``github:<login>``, ``gitlab:<username>`` (the forge lower-cased) or bare, without
    an ``@``; None when it names no one (:func:`parse_owner`)."""
    name = parse_owner(text)
    return None if name is None else str(name)
