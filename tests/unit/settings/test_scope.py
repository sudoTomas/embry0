"""Which settings document a name points at (spec 2026-10-29, #161)."""

from __future__ import annotations

import pytest

from embry0.settings.scope import Scope, ScopeError, current, owner_of, stored_rows
from embry0.storage import repo_renames


def test_each_document_has_one_scope_string() -> None:
    assert str(Scope.deployment()) == "deployment"
    assert str(Scope.organization("Acme")) == "organization:acme", "an owner is stored lowercase, as organizations are"
    assert str(Scope.repository("Acme/Widgets")) == "repository:acme/widgets"
    assert str(Scope.repository("group/sub/project")) == "repository:group/sub/project"


def test_a_repository_sits_under_the_first_segment_of_its_slug() -> None:
    """A GitLab project in a subgroup belongs to the top group, the organization embry0 registers."""
    assert Scope.repository("group/sub/project").owner == "group"
    assert Scope.repository("group/sub/project").parent == Scope.organization("group")
    assert Scope.organization("acme").parent == Scope.deployment()
    assert Scope.deployment().parent is None
    assert owner_of(" Acme/widgets ") == "acme"


@pytest.mark.parametrize("stored", ["deployment", "organization:acme", "repository:acme/widgets"])
def test_a_stored_scope_reads_back_as_itself(stored: str) -> None:
    assert str(Scope.parse(stored)) == stored


@pytest.mark.parametrize("owner", ["", "acme/widgets", "ac me", "-acme"])
def test_a_name_that_cannot_be_an_owner_is_refused(owner: str) -> None:
    with pytest.raises(ScopeError, match="is not an organization"):
        Scope.organization(owner)


@pytest.mark.parametrize("repo", ["acme", "acme/", "/widgets", "acme/../widgets", "acme/wid gets"])
def test_a_name_that_cannot_be_a_repository_is_refused(repo: str) -> None:
    with pytest.raises(ScopeError, match="is not a repository"):
        Scope.repository(repo)


@pytest.mark.parametrize("stored", ["team:acme", "organization:", "repository:acme", "acme"])
def test_a_scope_string_nothing_writes_is_refused(stored: str) -> None:
    with pytest.raises(ScopeError):
        Scope.parse(stored)


def test_a_renamed_repository_reads_its_new_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"acme/old-widgets": "Acme/Widgets"})
    assert Scope.repository("acme/old-widgets").name == "acme/widgets"
    assert Scope.parse("repository:acme/old-widgets").name == "acme/old-widgets", "a stored scope is read as stored"


def test_the_view_names_the_layer() -> None:
    assert Scope.repository("acme/widgets").view() == {"layer": "repository", "owner": "acme", "repo": "acme/widgets"}
    assert Scope.organization("acme").view() == {"layer": "organization", "owner": "acme", "repo": None}
    assert Scope.deployment().view() == {"layer": "deployment", "owner": None, "repo": None}


def test_a_repository_reads_its_own_row_else_the_first_under_an_old_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"acme/old-a": "acme/widgets", "acme/old-b": "acme/widgets"})
    index, shadowed = stored_rows(["repository:acme/old-b", "repository:acme/old-a", "organization:acme"])
    assert index == {"repository:acme/widgets": "repository:acme/old-a"}
    assert shadowed == [("repository:acme/old-b", "repository:acme/old-a")], (
        "the losing row is named, not dropped quietly"
    )
    index, shadowed = stored_rows(["repository:acme/old-a", "repository:acme/widgets"])
    assert index == {"repository:acme/widgets": "repository:acme/widgets"}, "a row under the current slug wins"
    assert shadowed == [("repository:acme/old-a", "repository:acme/widgets")]
    assert current(Scope.parse("repository:acme/old-a")) == Scope.repository("acme/widgets")
    assert current(Scope.organization("acme")) == Scope.organization("acme")
