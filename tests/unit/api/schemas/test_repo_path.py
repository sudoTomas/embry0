"""The one repo-path rule, shared by every schema that names a repo (spec 3.5)."""

import pytest

from embry0.api.schemas.repo_path import REPO_PATH_PATTERN, validate_repo_path


@pytest.mark.parametrize(
    "value",
    [
        "owner/name",
        "acme/products/fbr",
        "acme/group/backend",
        "a.b/c-d_e/f.g",
        "0rg/9ame",
        "acme/.github",
        ".acme/widgets",
        "acme/...",
    ],
)
def test_accepts_two_or_more_segments(value):
    assert validate_repo_path(value) == value
    assert REPO_PATH_PATTERN.match(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "owner",
        "/name",
        "owner/",
        "owner//name",
        "owner/../name",
        "-owner/name",
        "_owner/name",
        "owner/.",
        "owner/..",
        "./name",
        "../name",
        "owner/./name",
        "owner/na me",
        "owner/na\tme",
        "owner/name?x=1",
    ],
)
def test_rejects_malformed_paths(value):
    with pytest.raises(ValueError, match="group/subgroup/name"):
        validate_repo_path(value)


def test_rejects_a_trailing_newline():
    """``\\Z``, not ``$`` — ``$`` matches before a trailing newline, and the
    repo string flows into clone URLs and sanitised container/volume names."""
    assert REPO_PATH_PATTERN.match("owner/name\n") is None
    with pytest.raises(ValueError, match="group/subgroup/name"):
        validate_repo_path("owner/name\n")


def test_a_leading_dot_is_a_repository_but_dot_and_dot_dot_are_not():
    """GitHub's organization profile repository is ``.github``; only ``.`` and ``..`` themselves walk a path."""
    assert validate_repo_path("acme/.github") == "acme/.github", "a repository name may start with a dot"
    for value in ("acme/.", "acme/..", "acme/../widgets"):
        with pytest.raises(ValueError, match="group/subgroup/name"):
            validate_repo_path(value)


@pytest.mark.parametrize("value", ["owner/-", "owner/-/name", "group/sub/-", "-/name"])
def test_a_dash_is_never_a_repository_segment(value):
    """``-`` is the separator before a repository's sub-resources (#257): a repository holding it could not be told
    from one, so ``/repos/owner/-/name`` would be both the repository ``owner/-/name`` and ``name`` of ``owner``."""
    assert REPO_PATH_PATTERN.match(value) is None, f"{value!r} must name no repository: '-' is the separator"
    with pytest.raises(ValueError, match="group/subgroup/name"):
        validate_repo_path(value)
