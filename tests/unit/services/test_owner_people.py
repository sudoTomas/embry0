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
    def __init__(self, *rows: Any) -> None:
        self.rows = rows

    async def people(self) -> list[Any]:
        return list(self.rows)


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
