"""Clone origin follows the repo's forge (#166)."""

import pytest
import structlog

from embry0.workspace_init.git import DEFAULT_CLONE_HOST, build_clone_shell, clone_origin

SUBGROUP = "acme/products/fbr"


def test_defaults_to_github():
    assert clone_origin() == DEFAULT_CLONE_HOST == "https://github.com"
    assert clone_origin("github") == "https://github.com"


def test_gitlab_default_origin():
    assert clone_origin("gitlab") == "https://gitlab.com"


def test_self_managed_gitlab_overrides_the_default():
    assert clone_origin("gitlab", "https://git.internal.example/") == "https://git.internal.example"


def test_gitlab_url_is_ignored_for_github():
    """GitHub has no equivalent knob; a stray value must not redirect it."""
    assert clone_origin("github", "https://git.internal.example") == "https://github.com"


def test_unknown_forge_falls_back_to_github():
    assert clone_origin("bitbucket") == "https://github.com"


def test_clone_shell_uses_the_gitlab_origin_and_nested_path():
    shell = build_clone_shell(SUBGROUP, forge="gitlab")
    assert f"git clone --depth=50 https://gitlab.com/{SUBGROUP}.git /workspace" in shell


def test_clone_shell_is_unchanged_for_github():
    """Regression guard: the default path must be byte-identical to before."""
    assert build_clone_shell("owner/repo") == build_clone_shell("owner/repo", forge="github")
    assert "https://github.com/owner/repo.git" in build_clone_shell("owner/repo")


def test_ref_checkout_still_appended_for_gitlab():
    shell = build_clone_shell(SUBGROUP, "FBT_Source", forge="gitlab")
    assert "https://gitlab.com/" in shell
    assert "FBT_Source" in shell


# --- Spec 1A §3.7: the fetch follows the repo's base branch ------------------


def test_clone_shell_defaults_to_fetching_main():
    """Regression guard: the historical line must be byte-identical."""
    assert "git -C /workspace fetch origin main:main --depth=50" in build_clone_shell("owner/repo")


def test_clone_shell_fetches_the_configured_base_branch():
    shell = build_clone_shell(SUBGROUP, forge="gitlab", base_branch="dev")
    assert "git -C /workspace fetch origin dev:dev --depth=50" in shell
    assert "main:main" not in shell


def test_base_branch_fetch_stays_brace_scoped_and_best_effort():
    """`a && b || true` rescues the WHOLE chain — a failed clone would exit 0
    over an empty /workspace (2026-07-06 access smoke)."""
    shell = build_clone_shell("owner/repo", base_branch="release/2026.09")
    assert "&& { git -C /workspace fetch origin release/2026.09:release/2026.09 --depth=50 || true; }" in shell


@pytest.mark.parametrize("bad", ["feat branch", "a;rm -rf /", "", "--upload-pack=evil", "dev\n"])
def test_unsafe_base_branch_falls_back_to_main_instead_of_reaching_the_shell(bad):
    assert "fetch origin main:main" in build_clone_shell("owner/repo", base_branch=bad)


def test_rejected_base_branch_is_logged_with_the_offending_value():
    """A silent fallback leaves an operator staring at a job diffing against
    main with nothing in the logs to explain it."""
    with structlog.testing.capture_logs() as captured:
        shell = build_clone_shell("owner/repo", base_branch="a;rm -rf /")
    assert "fetch origin main:main" in shell
    event = next(e for e in captured if e["event"] == "base_branch_rejected")
    assert event["log_level"] == "warning"
    assert event["base_branch"] == "a;rm -rf /"


def test_valid_base_branch_logs_nothing():
    with structlog.testing.capture_logs() as captured:
        build_clone_shell("owner/repo", base_branch="dev")
    assert captured == []


def test_ref_checkout_is_unaffected_by_the_base_branch():
    shell = build_clone_shell(SUBGROUP, "embry0/iss-abc-fix", forge="gitlab", base_branch="dev")
    assert "fetch origin dev:dev" in shell
    assert "checkout -B embry0/iss-abc-fix" in shell
