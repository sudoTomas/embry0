"""Owners resolve to people (#179): who an owner login is, on which forge, and who is asked as what."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.people_directory import person

try:
    from embry0.services.owner_people import Directory, chat, read_directory, unknown
except ImportError:
    pytest.fail("owners resolve to no people: there is no embry0.services.owner_people", pytrace=False)

ADA = person("Ada", email="ada@example.com", identities=[("gitlab", "ada"), ("telegram", "ada_tg"), ("slack", "U1")])
BO = person("Bo", identities=[("github", "Bo-L")])
GONE = person("Gone", email="gone@example.com", identities=[("github", "gone")], disabled=True)
FORGES = {"acme/web": "gitlab", "globex/gizmos": "github"}


def _directory(*people: Any) -> Directory:
    return Directory(people=people or (ADA, BO, GONE), forges=FORGES)


def test_an_owner_is_a_login_on_its_repository_s_forge() -> None:
    d = _directory()
    assert d.holder("acme/web", "@Ada") is ADA, "a bare owner of a GitLab organization's repository is gitlab:ada"
    assert d.holder("globex/gizmos", "ada") is None, "the same text on another forge is someone else, or nobody"
    assert d.forge_of("initech/app") == "github", "a repository nothing names a forge for reads GitHub's"


def test_who_an_owner_is_asked_as() -> None:
    d = _directory()
    assert d.recipient("acme/web", "ADA") == "ada@example.com", "a person by their email"
    assert d.recipient("globex/gizmos", "bo-l") == "github:bo-l", "a person with no email as <forge>:<login>"
    assert d.recipient("acme/web", "cy") == "gitlab:cy", "a login no person holds as itself, on its forge"
    assert d.recipient("globex/gizmos", "gone") is None, "a disabled person is not asked"
    assert unknown("gitlab:cy", d) and not unknown("github:bo-l", d), "only a login no person holds is unknown"
    assert not unknown("ada@example.com", d) and not unknown("group:triage", d)


def test_a_person_owns_a_decision_through_an_identity_on_its_forge() -> None:
    d = _directory()
    assert d.owns(ADA, {"repo": "acme/web", "owners": ["@ada"]})
    assert not d.owns(ADA, {"repo": "globex/gizmos", "owners": ["ada"]}), "a GitLab login owns nothing on GitHub"
    assert not d.owns(GONE, {"repo": "globex/gizmos", "owners": ["gone"]}), "a disabled person owns nothing"
    assert not d.owns(None, {"repo": "acme/web", "owners": ["ada"]})
    assert d.live_owners({"repo": "globex/gizmos", "owners": ["gone", "Bo-L"]}) == ["bo-l"], (
        "a login a disabled person holds is no owner"
    )


def test_an_owner_named_with_its_forge_is_a_login_on_that_forge_whatever_the_organization_s() -> None:
    """#181: ``github:<login>`` and ``gitlab:<username>`` name their forge; only a bare owner is the organization's."""
    d = _directory()
    assert d.holder("globex/gizmos", "gitlab:@ADA") is ADA, "a GitLab owner of a GitHub organization's repository"
    assert d.holder("acme/web", "github:bo-l") is BO, "a GitHub owner of a GitLab organization's repository"
    assert d.holder("acme/web", "github:ada") is None, "Ada's GitLab login is not a GitHub one"
    assert d.recipient("globex/gizmos", "gitlab:ada") == "ada@example.com"
    assert d.recipient("acme/web", "GitHub:Cy") == "github:cy", "a login no person holds, as itself, on its forge"
    assert d.owns(ADA, {"repo": "globex/gizmos", "owners": ["gitlab:ada"]})
    assert d.owns(BO, {"repo": "acme/web", "owners": ["github:@bo-l"]})
    assert not d.owns(BO, {"repo": "acme/web", "owners": ["bo-l"]}), "bare: a GitLab login on a GitLab repository"
    assert d.live_owners({"repo": "acme/web", "owners": ["github:gone", "gitlab:ada"]}) == ["ada"], (
        "a named owner a disabled person holds is no owner either"
    )


def test_a_recipient_key_names_its_person() -> None:
    d = _directory()
    assert d.of_recipient("ADA@example.com") is ADA
    assert d.of_recipient("github:bo-l") is BO
    assert d.of_recipient("group:triage") is None and d.of_recipient("gitlab:cy") is None


def test_a_person_s_chat_handles_in_order() -> None:
    assert chat(ADA) == [{"channel": "telegram", "id": "ada_tg"}, {"channel": "slack", "id": "U1"}], (
        "the chat identities, never a forge login"
    )
    assert chat(BO) == [] and chat(None) == []


class _People:
    def __init__(self, *people: Any) -> None:
        self.people = people

    async def all(self, *, include_disabled: bool = False) -> list[Any]:
        assert include_disabled, "a disabled person is read too: their logins are no owners"
        return list(self.people)


class _Organizations:
    async def list_all(self) -> list[Any]:
        return [SimpleNamespace(owner="Acme", forge="gitlab")]


class _Repos:
    async def forge_or_none(self, repo: str) -> str | None:
        return {"initech/app": "gitlab"}.get(repo)


class _Users:
    """Users as the repository reads them: ``people()`` those not disabled, ``disabled_ids()`` the rest."""

    def __init__(self, *rows: Any, disabled: tuple[str, ...] = ()) -> None:
        self.rows, self.disabled = rows, set(disabled)

    async def people(self) -> list[Any]:
        return [u for u in self.rows if u.id not in self.disabled]

    async def disabled_ids(self) -> set[str]:
        return set(self.disabled)


async def test_each_repository_s_forge_is_its_organization_s_else_its_own_else_github() -> None:
    d = await read_directory(_People(), _Organizations(), _Repos(), ["acme/web", "Initech/App", "umbrella/x", None])
    assert (d.forge_of("ACME/web"), d.forge_of("initech/app"), d.forge_of("umbrella/x")) == (
        "gitlab",
        "gitlab",
        "github",
    ), "the organization's forge, else the repository's own registration's, else GitHub's"


async def test_a_user_with_no_person_stands_in_with_their_github_login() -> None:
    """For one release (#179): a user who signed in after the people were made, not yet a person, keeps being asked
    by their email and owning what their GitHub login owns. A person holding the login wins."""
    linked = person("Lin", email="lin@example.com", identities=[("github", "lin")], user_id="usr-lin")
    holder = person("Held", email="held@example.com", identities=[("github", "taken")])
    users = _Users(
        SimpleNamespace(id="usr-new", email="new@example.com", name="New", github_login="newbie"),
        SimpleNamespace(id="usr-lin", email="lin@example.com", name="Lin", github_login="lin"),
        SimpleNamespace(id="usr-dup", email="dup@example.com", name="Dup", github_login="taken"),
        SimpleNamespace(id="usr-none", email="none@example.com", name="None", github_login=None),
    )
    d = await read_directory(_People(linked, holder), None, None, ["globex/gizmos"], users)
    assert d.recipient("globex/gizmos", "newbie") == "new@example.com", "the user stands in, asked by email"
    assert d.owns(d.by_email("new@example.com"), {"repo": "globex/gizmos", "owners": ["newbie"]})
    assert d.holder("globex/gizmos", "lin") is linked, "a user who is a person is that person, not a stand-in"
    assert d.holder("globex/gizmos", "taken") is holder, "a person holding the login wins over a user's"
    assert d.by_email("none@example.com") is None, "a user with no login stands in for nothing"


async def test_a_person_whose_sign_in_is_disabled_reads_as_disabled_and_a_disabled_user_stands_in_for_nobody() -> None:
    linked = person("Lin", email="lin@example.com", identities=[("github", "lin")], user_id="usr-lin")
    users = _Users(
        SimpleNamespace(id="usr-lin", email="lin@example.com", name="Lin", github_login="lin"),
        SimpleNamespace(id="usr-off", email="off@example.com", name="Off", github_login="offy"),
        disabled=("usr-lin", "usr-off"),
    )
    d = await read_directory(_People(linked), None, None, ["globex/gizmos"], users)
    assert d.recipient("globex/gizmos", "lin") is None, "a person whose sign-in is disabled is not asked"
    assert not d.owns(d.by_email("lin@example.com"), {"repo": "globex/gizmos", "owners": ["lin"]})
    assert d.holder("globex/gizmos", "offy") is None, "a disabled user stands in for nobody"


def test_a_caller_is_every_person_their_email_and_sign_in_name_and_owns_only_as_a_login_s_holder() -> None:
    holder = person("Alice", email="alice@corp.example", identities=[("github", "alice")])
    stand_in = person("U", email="u@x.example", identities=[("github", "alice")], user_id="usr-u")
    d = Directory(people=(stand_in, holder), forges=FORGES)
    decision = {"repo": "globex/gizmos", "owners": ["alice"]}
    claimants = getattr(d, "claimants", None)
    assert claimants is not None, "a caller is whoever their email names, never whoever their sign-in is too"
    assert [p.id for p in claimants("u@x.example", "usr-u")] == [stand_in.id]
    assert not d.owns(stand_in, decision), "the same login on a user standing in owns nothing a person holds"
    assert d.owns(holder, decision)


def test_an_owner_login_nobody_holds_is_suggested_on_its_repository_s_forge() -> None:
    """The People page's suggestions (#179): an owner login is offered on the forge its repository's organization is
    on, and is no suggestion once a person holds it there; holding the same text on another forge does not count."""
    from embry0.services import owner_people

    unheld_owners = getattr(owner_people, "unheld_owners", None)
    assert unheld_owners is not None, "the suggestions say no owner's forge: there is no unheld_owners"
    rows = [
        {"repo": "acme/web", "owner": "@Ada"},  # gitlab:ada, Ada's
        {"repo": "acme/web", "owner": "cy"},
        {"repo": "acme/api", "owner": "CY"},
        {"repo": "globex/gizmos", "owner": "ada"},  # github:ada: nobody's
        {"repo": "globex/gizmos", "owner": "gone"},  # a disabled person's: held all the same
        {"repo": "globex/gizmos", "owner": ""},
    ]
    d = Directory(people=(ADA, BO, GONE), forges={**FORGES, "acme/api": "gitlab"})
    assert unheld_owners(rows, d) == [
        {"forge": "github", "login": "ada", "repos": ["globex/gizmos"]},
        {"forge": "gitlab", "login": "cy", "repos": ["acme/api", "acme/web"]},
    ]
