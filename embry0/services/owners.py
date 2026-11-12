"""Who answers for a piece of work: one rule, read by every place that names owners (spec 2026-11-10).

An area's owners when its area names some; else the repository's own: its
``owners`` preference, else the users its ``CODEOWNERS`` names for ``*``.
All of them are logins (:mod:`embry0.forge_logins`). The owner proposals
feed, the decisions and, through them, the asks read owners only here, so
the one place a login is turned into a person is this one (#179).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from embry0.services.areas import Area

OwnerSource = Literal["area", "repository", "codeowners"]


@dataclass(frozen=True)
class Owners:
    """The logins asked about a piece of work, and where they came from; ``source`` None when nobody is."""

    logins: tuple[str, ...] = ()
    source: OwnerSource | None = None


def repository_owners(
    repos: Iterable[str], preferences: Mapping[str, Sequence[str]], codeowners: Mapping[str, Sequence[str]]
) -> dict[str, Owners]:
    """Each of ``repos``' own owners (lower-cased): its preference when it names some, else its ``CODEOWNERS``."""
    out: dict[str, Owners] = {}
    for repo in repos:
        name = repo.lower()
        if preferences.get(name):
            out[name] = Owners(tuple(preferences[name]), "repository")
        elif codeowners.get(name):
            out[name] = Owners(tuple(codeowners[name]), "codeowners")
        else:
            out[name] = Owners()
    return out


def owners_for(area: Area | None, repository: Owners) -> Owners:
    """The owners of work in ``area`` (None: none): the area's when it names some, else ``repository``."""
    if area is not None and area.owners:
        return Owners(tuple(area.owners), "area")
    return repository
