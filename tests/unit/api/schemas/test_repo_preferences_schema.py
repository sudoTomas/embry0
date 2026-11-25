"""A repository's owners preference (owner proposals, spec 2026-10-24): GitHub logins, cleaned."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from embry0.api.schemas.repo_preferences import RepoPreferencesUpdateRequest


def test_owners_are_logins_without_the_at_sign_or_repeats() -> None:
    body = RepoPreferencesUpdateRequest(owners=["@Octo", "pat", "octo", " lee-1 "])
    assert body.owners == ["Octo", "pat", "lee-1"]


def test_no_owners_falls_back_to_codeowners() -> None:
    assert RepoPreferencesUpdateRequest(owners=[]).owners is None
    assert RepoPreferencesUpdateRequest(owners=None).owners is None


@pytest.mark.parametrize("login", ["-octo", "octo-", "oc--to", "octo cat", "acme/team", "a" * 40, ""])
def test_a_value_that_is_no_login_is_refused(login: str) -> None:
    with pytest.raises(ValidationError, match="not a GitHub login"):
        RepoPreferencesUpdateRequest(owners=[login])


def test_an_owner_may_be_named_with_its_forge() -> None:
    """#181: ``github:<login>`` and ``gitlab:<username>`` (each checked by its forge's pattern) beside bare logins,
    stored as written with the forge lower-cased; the same owner written twice is kept once."""
    body = RepoPreferencesUpdateRequest(owners=["GitLab:@bob_s", "gitlab:bob_s", "github:Octo", "octo", "@pat"])
    assert body.owners == ["gitlab:bob_s", "github:Octo", "octo", "pat"]


@pytest.mark.parametrize("owner", ["bob_s", "github:bob_s", "gitlab:-bob", "bitbucket:ada"])
def test_an_owner_no_forge_would_take_is_refused(owner: str) -> None:
    """A bare owner is checked by GitHub's pattern, as before forges were named: a GitLab username GitHub would not
    take is written gitlab:<username>."""
    with pytest.raises(ValidationError, match="is not an owner"):
        RepoPreferencesUpdateRequest(owners=[owner])
