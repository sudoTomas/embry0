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
