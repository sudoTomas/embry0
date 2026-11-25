"""Owners resolve to people (#179, spec 2026-11-23 sections 4 and 5).

An owner is a login, as a repository's preferences, an area or
``CODEOWNERS`` names it (:func:`embry0.services.owners.owners_for`), named
with its forge (``github:<login>``, ``gitlab:<username>``, #181) or bare. The
forge a bare one is a login on is the repository's organization's: a bare
owner of a GitLab organization's repository is ``gitlab:<login>``; a named one
is on its own forge, whatever the organization's. That pair is looked up
among the people's identities, so asks and who may decide receive people:

- **Who is asked.** A person is asked by their email, and one with no email
  as ``<forge>:<login>``. A login no person holds is asked as
  ``<forge>:<login>`` too, marked unknown: embry0 makes no person by itself
  (D6). A disabled person is neither asked nor an owner.
- **Who may decide.** A person owns a decision when one of their identities
  is one of its owners on its repository's forge.
- **When.** A person's ``timezone``, ``hours`` and ``weekdays`` win over the
  organization's, field by field (D3); a group follows the organization.
- **Where.** A person's chat handles are their identities of a chat's kind.

Everything here reads one :class:`Directory`, built once a tick or a request
(:func:`read_directory`): the people, and each repository's forge.

**A user with no person, for one release.** A user who signed in after the
people were made from the users (migration 154) has no person until someone
adds one, but may have a GitHub login (``users.github_login``), which owners
were matched by until now. Such a user stands in as a person with that one
``github`` identity, read and never stored, so they are asked and decide as
before; a person holding the login wins. This goes with the column.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from embry0.forge_logins import split_owner
from embry0.settings.scope import organization_of
from embry0.storage.repositories.people import Identity, Person

#: The forge an owner's login is read on when nothing names the repository's: the one owners were before.
DEFAULT_FORGE = "github"
#: The identities that are a chat's handle for a person, in the order they are served.
CHAT_KINDS = ("telegram", "slack", "teams", "discord")
#: The prefix of a recipient that is a group (``asks.unowned_group``), not a person.
GROUP = "group:"


def login_of(owner: Any) -> str:
    """An owner's login as stored, without its forge and its ``@``, lowercase: how it is compared."""
    return split_owner(owner)[1]


@dataclass(frozen=True)
class Directory:
    """The deployment's people, by identity, email and sign-in, and the forge of each repository asked about."""

    people: tuple[Person, ...] = ()
    #: Repository (lowercase) to the forge its owners' logins are on.
    forges: Mapping[str, str] = field(default_factory=dict)
    _by_identity: dict[tuple[str, str], Person] = field(default_factory=dict, init=False, repr=False)
    _by_email: dict[str, Person] = field(default_factory=dict, init=False, repr=False)
    _by_user: dict[str, Person] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        # In order: a stand-in for a user (listed first) gives way to a person holding the same value.
        for person in self.people:
            for identity in person.identities:
                self._by_identity[(identity.kind, identity.value.lower())] = person
            if person.email:
                self._by_email[person.email.lower()] = person
            if person.user_id:
                self._by_user[person.user_id] = person

    def forge_of(self, repo: str | None) -> str:
        """The forge ``repo``'s bare owners are logins on: its organization's, else its own, else GitHub."""
        return self.forges.get(str(repo or "").lower()) or DEFAULT_FORGE

    def handle(self, repo: str | None, owner: Any) -> tuple[str, str]:
        """``owner`` of ``repo`` as the identity it names, ``(forge, login)``: a named one's own forge
        (``gitlab:<username>``), a bare one's on ``repo``'s forge (:meth:`forge_of`)."""
        forge, login = split_owner(owner)
        return forge or self.forge_of(repo), login

    def holder(self, repo: str | None, owner: Any) -> Person | None:
        """The person whose identity ``owner`` of ``repo`` names (:meth:`handle`), disabled or not; None for
        nobody."""
        return self._by_identity.get(self.handle(repo, owner))

    def recipient(self, repo: str | None, owner: Any) -> str | None:
        """Who ``owner`` of a decision in ``repo`` is asked as: their person's email, else ``<forge>:<login>``,
        a login no person holds included; None for a disabled person, who is not asked."""
        person = self.holder(repo, owner)
        if person is not None and person.disabled:
            return None
        if person is not None and person.email:
            return person.email.lower()
        forge, login = self.handle(repo, owner)
        return f"{forge}:{login}"

    def by_email(self, email: str) -> Person | None:
        return self._by_email.get(email.strip().lower())

    def by_user(self, user_id: str | None) -> Person | None:
        return self._by_user.get(user_id) if user_id else None

    def of_recipient(self, key: str) -> Person | None:
        """The person a recipient key names: by email, or by a ``<forge>:<login>`` identity; None for a group or a
        login no person holds."""
        key = key.strip().lower()
        if key.startswith(GROUP):
            return None
        if "@" in key:
            return self.by_email(key)
        kind, _, value = key.partition(":")
        return self._by_identity.get((kind, value)) if value else None

    def live_owners(self, decision: Mapping[str, Any]) -> list[str]:
        """``decision``'s owners, as logins, but those a disabled person holds: those are no owners."""
        repo = decision.get("repo")
        return [
            login_of(owner)
            for owner in decision.get("owners") or []
            if not ((person := self.holder(repo, owner)) is not None and person.disabled)
        ]

    def claimants(self, email: str | None, user_id: str | None) -> list[Person]:
        """Every person a caller is: the one their email names, and the one their sign-in is (the same one, or a
        user standing in), each once."""
        found: list[Person] = []
        for person in (self.by_email(email) if email else None, self.by_user(user_id)):
            if person is not None and all(person.id != other.id for other in found):
                found.append(person)
        return found

    def owns(self, person: Person | None, decision: Mapping[str, Any]) -> bool:
        """Whether ``person`` holds one of ``decision``'s owners (:meth:`handle`: a named owner on its own forge, a
        bare one on its repository's): is the one the directory names for that login, not merely one with an
        identity of the same text (a user standing in for a login a person holds owns nothing). A disabled person
        owns nothing."""
        if person is None or person.disabled:
            return False
        repo = decision.get("repo")
        return any(
            (holder := self.holder(repo, owner)) is not None and holder.id == person.id
            for owner in decision.get("owners") or []
        )


def unknown(key: str, directory: Directory) -> bool:
    """Whether ``key`` names a login no person holds: asked as itself, for someone to add (D6)."""
    key = key.strip().lower()
    return not key.startswith(GROUP) and "@" not in key and directory.of_recipient(key) is None


def chat(person: Person | None) -> list[dict[str, str]]:
    """``person``'s chat handles, ``{channel, id}`` each, in :data:`CHAT_KINDS`' order and then as stored."""
    if person is None:
        return []
    rank = {kind: i for i, kind in enumerate(CHAT_KINDS)}
    handles = [i for i in person.identities if i.kind in rank]
    return [{"channel": i.kind, "id": i.value} for i in sorted(handles, key=lambda i: rank[i.kind])]


def _stand_ins(users: Iterable[Any], linked: set[str]) -> tuple[Person, ...]:
    """Each user not disabled with a GitHub login and no person, as a person holding that login (one release)."""
    return tuple(
        Person(
            id=f"user:{u.id}",
            name=u.name or u.email,
            email=u.email,
            user_id=u.id,
            timezone=None,
            hours=None,
            weekdays=None,
            disabled=False,
            created_by="",
            created_at=None,
            updated_at=None,
            identities=(Identity(kind="github", value=str(u.github_login)),),
        )
        for u in users
        if u.github_login and u.id not in linked
    )


def unheld_owners(owners: Iterable[Mapping[str, Any]], directory: Directory) -> list[dict[str, Any]]:
    """The owner logins nobody holds, from ``owners`` (``{repo, owner}`` each, as stored): each login on its forge
    (:meth:`Directory.handle`: a named owner's own, a bare one's repository's), ``{forge, login, repos}``, with the
    repositories naming it, by forge and login. What the People page offers to add (D6). Nobody means no person, and
    no user standing in for one by their GitHub login: that user is suggested as a user, whose Add takes the login."""
    found: dict[tuple[str, str], set[str]] = {}
    for row in owners:
        repo, owner = str(row.get("repo") or ""), row.get("owner")
        forge, login = directory.handle(repo, owner)
        if not login or directory.holder(repo, owner) is not None:
            continue
        found.setdefault((forge, login), set()).add(repo)
    return [{"forge": forge, "login": login, "repos": sorted(repos)} for (forge, login), repos in sorted(found.items())]


async def read_directory(
    people: Any, organizations: Any, repos: Any, asked_about: Iterable[str | None], users: Any = None
) -> Directory:
    """The people, and the forge of each repository in ``asked_about``: its organization's when the organization is
    registered, else the repository's own registration's, else GitHub's. ``people``, ``organizations``, ``repos``
    and ``users`` are the repositories; a missing one reads as none. A user with no person stands in as one, and a
    person whose user is disabled reads as disabled."""
    everyone = tuple(await people.all(include_disabled=True)) if people is not None else ()
    if users is not None:
        # A person whose sign-in is disabled is disabled with it; a disabled user stands in for nobody.
        off = await users.disabled_ids()
        everyone = tuple(replace(p, disabled=True) if p.user_id in off and not p.disabled else p for p in everyone)
        linked = {p.user_id for p in everyone if p.user_id}
        everyone = (*_stand_ins(await users.people(), linked), *everyone)
    return Directory(people=everyone, forges=await forges_of(organizations, repos, asked_about))


async def forges_of(organizations: Any, repos: Any, asked_about: Iterable[str | None]) -> dict[str, str]:
    """The forge each repository in ``asked_about`` (lower-cased) has its bare owners on: its organization's when
    the organization is registered, else the repository's own registration's, else GitHub's."""
    names = {str(repo).lower() for repo in asked_about if repo}
    forges: dict[str, str] = {}
    by_owner: dict[str, str] = {}
    if organizations is not None and names:
        by_owner = {str(o.owner).lower(): str(o.forge) for o in await organizations.list_all()}
    for repo in sorted(names):
        forge = by_owner.get(organization_of(repo))
        if forge is None and repos is not None:
            forge = await repos.forge_or_none(repo)
        forges[repo] = forge or DEFAULT_FORGE
    return forges


async def directory_of(state: Any, asked_about: Iterable[str | None]) -> Directory:
    """:func:`read_directory` over the application's repositories (``app.state``)."""
    return await read_directory(
        getattr(state, "people_repo", None),
        getattr(state, "organizations_repo", None),
        getattr(state, "repos_repo", None),
        asked_about,
        getattr(state, "users_repo", None),
    )
