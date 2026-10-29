"""Which forge owners a key's repository allowlist covers (#180): the owners of the repositories on it.

Something that belongs to an owner rather than to one repository (a
knowledge map) is checked against these, as a repository is against the list.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from unittest.mock import patch


def _owner_allowed() -> Callable[[Iterable[str] | None, str | None], bool]:
    from embry0.api import repo_allowlist

    found = getattr(repo_allowlist, "owner_allowed", None)
    assert found is not None, "repo_allowlist must say whether a key's allowlist covers a forge owner"
    return found  # type: ignore[no-any-return]


def test_a_key_covers_the_owners_of_its_repositories_and_no_other() -> None:
    allowed = _owner_allowed()
    assert allowed(None, "acme") and allowed(None, None), "a key without an allowlist covers every owner"
    assert allowed(("Acme/Widgets", "zeta/gears"), "acme"), "matching ignores case"
    assert allowed(("acme/widgets",), "ACME")
    assert not allowed(("acme/widgets",), "zeta"), "a key must not cover an owner none of its repositories is under"
    assert not allowed(("acme/widgets",), "acme-labs"), "a prefix is not a match"
    assert not allowed(("acme-labs/widgets",), "acme")
    assert not allowed(("acme/widgets",), None), "a restricted key covers no map that belongs to no one"
    assert not allowed(("acme/widgets",), "")


def test_a_renamed_repository_covers_its_old_and_its_current_owner_both_ways() -> None:
    allowed = _owner_allowed()
    with patch("embry0.storage.repo_renames.REPO_RENAMES", {"old-acme/widgets": "acme/widgets"}):
        assert allowed(("old-acme/widgets",), "acme"), "an entry naming the old slug covers the current owner"
        assert allowed(("old-acme/widgets",), "old-acme"), "and the owner it names"
        assert allowed(("acme/widgets",), "old-acme"), "an entry naming the current slug covers the old owner"
        assert not allowed(("acme/gears",), "old-acme"), "a repository that was not renamed covers only its owner"
