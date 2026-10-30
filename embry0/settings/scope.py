"""Which settings document a value is read from or written to (spec 2026-10-29).

The deployment has one document. An organization and a repository may each
have a layer of their own, a document of the same shape holding only what
that organization or repository sets. A layer is stored under a scope
string: ``deployment``, ``organization:<owner>`` or
``repository:<owner>/<name>``.

The organization of a repository is the first segment of its slug,
lowercased, as everywhere else in embry0: a GitLab project
``group/sub/project`` belongs to the organization ``group``. A repository
slug is stored lowercased, with the deployment's ``REPO_RENAMES`` applied, so
a layer written under a repository's old slug still applies after a rename.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

from embry0.storage.repo_renames import normalize_repo_slug

Layer = Literal["deployment", "organization", "repository"]

DEPLOYMENT: Final = "deployment"
_ORGANIZATION: Final = "organization:"
_REPOSITORY: Final = "repository:"

#: A forge owner as organizations store it: lowercase, no slash. The same
#: pattern is migration 134's check on ``settings_documents.scope``.
_OWNER = re.compile(r"[a-z0-9_][a-z0-9._-]*")
_SEGMENT = re.compile(r"[a-z0-9._-]+")

#: How a 422 or a message names the layer: "An organization cannot set ...".
LAYER_NOUNS: Final[dict[str, str]] = {"organization": "An organization", "repository": "A repository"}


class ScopeError(ValueError):
    """A name that cannot be an organization or a repository."""


def owner_of(repo: str) -> str:
    """The forge owner a repository sits under: its first path segment, lowercase."""
    return repo.strip().split("/", 1)[0].lower()


def _owner(raw: str) -> str:
    owner = raw.strip().lower()
    if not _OWNER.fullmatch(owner):
        raise ScopeError(
            f"{raw.strip()[:100]!r} is not an organization: a forge owner, with no slash "
            "(letters, digits, '.', '_' and '-')"
        )
    return owner


def _repo(raw: str) -> str:
    repo = raw.strip().lower()
    parts = repo.split("/")
    if (
        len(parts) < 2
        or not _OWNER.fullmatch(parts[0])
        or any(not _SEGMENT.fullmatch(part) or part in {".", ".."} for part in parts[1:])
    ):
        raise ScopeError(f"{raw.strip()[:200]!r} is not a repository: write it as owner/name")
    return repo


@dataclass(frozen=True)
class Scope:
    """One settings document: the deployment's, an organization's or a repository's."""

    layer: Layer
    #: The owner of an organization, the ``owner/name`` of a repository; empty for the deployment.
    name: str = ""

    @staticmethod
    def deployment() -> Scope:
        return Scope(DEPLOYMENT)

    @staticmethod
    def organization(owner: str) -> Scope:
        """The layer of ``owner``; :class:`ScopeError` when it cannot be a forge owner."""
        return Scope("organization", _owner(owner))

    @staticmethod
    def repository(repo: str) -> Scope:
        """The layer of ``repo``, its slug lowercased with ``REPO_RENAMES`` applied."""
        return Scope("repository", _repo(normalize_repo_slug(_repo(repo))))

    @staticmethod
    def parse(stored: str) -> Scope:
        """The scope a stored scope string names, as stored (no rename applied)."""
        if stored == DEPLOYMENT:
            return Scope.deployment()
        if stored.startswith(_ORGANIZATION):
            return Scope("organization", _owner(stored.removeprefix(_ORGANIZATION)))
        if stored.startswith(_REPOSITORY):
            return Scope("repository", _repo(stored.removeprefix(_REPOSITORY)))
        raise ScopeError(f"{stored[:200]!r} is not a settings scope")

    @property
    def owner(self) -> str | None:
        """The organization this layer is, or sits under; None for the deployment."""
        if self.layer == "organization":
            return self.name
        if self.layer == "repository":
            return owner_of(self.name)
        return None

    @property
    def parent(self) -> Scope | None:
        """The layer this one inherits from: a repository's organization, an organization's deployment."""
        if self.layer == "repository":
            return Scope("organization", owner_of(self.name))
        if self.layer == "organization":
            return Scope.deployment()
        return None

    def __str__(self) -> str:
        if self.layer == "organization":
            return _ORGANIZATION + self.name
        if self.layer == "repository":
            return _REPOSITORY + self.name
        return DEPLOYMENT

    def view(self) -> dict[str, str | None]:
        """How an answer names the layer: ``{"layer", "owner", "repo"}``."""
        return {
            "layer": self.layer,
            "owner": self.owner,
            "repo": self.name if self.layer == "repository" else None,
        }


def organization_of(repo: str) -> str:
    """The organization whose layer ``repo`` reads: the owner of its current slug, ``REPO_RENAMES`` applied.

    A name that cannot be a repository's slug falls back to its first
    segment, lowercase, as :func:`owner_of` reads it.
    """
    try:
        return owner_of(Scope.repository(repo).name)
    except ScopeError:
        return owner_of(repo)


def current(scope: Scope) -> Scope:
    """``scope`` under its repository's current slug, with ``REPO_RENAMES`` as installed now; any other as it is.

    Read at the time of the call, not when a snapshot was built: the
    orchestrator installs the renames after it reads the first snapshot.
    """
    if scope.layer != "repository":
        return scope
    try:
        return Scope.repository(scope.name)
    except ScopeError:
        return scope


def stored_rows(keys: Iterable[str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Which stored repository layer each repository reads, by its current scope string.

    ``keys`` are stored scope strings. A repository reads the row stored
    under its current slug when there is one, else the first row (by scope)
    stored under a slug ``REPO_RENAMES`` maps to it. Also answers the rows
    that lose, ``(stored, read instead)``: a layer nothing reads.
    """
    parsed: list[tuple[str, str]] = []
    for key in sorted(keys):
        try:
            scope = Scope.parse(key)
        except ScopeError:
            continue
        if scope.layer == "repository":
            parsed.append((key, str(current(scope))))
    index = {now: key for key, now in parsed if key == now}
    shadowed: list[tuple[str, str]] = []
    for key, now in parsed:
        if key == now:
            continue
        if now in index:
            shadowed.append((key, index[now]))
        else:
            index[now] = key
    return index, shadowed
