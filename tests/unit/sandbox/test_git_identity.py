"""Tests for sandbox git identity resolution (EMB-51)."""

from __future__ import annotations

from embry0.branding import GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME
from embry0.sandbox.git_identity import (
    GitIdentity,
    build_git_identity_cmd,
    default_git_identity,
    resolve_git_identity,
)


class _FakePrefsRepo:
    def __init__(self, row=None, raises=False):
        self.row = row
        self.raises = raises

    async def get(self, repo):
        if self.raises:
            raise RuntimeError("db down")
        return self.row


async def test_default_without_prefs_repo():
    identity = await resolve_git_identity(None, "owner/repo")
    assert identity == GitIdentity(name=GIT_AUTHOR_NAME, email=GIT_AUTHOR_EMAIL)


async def test_default_when_no_row():
    identity = await resolve_git_identity(_FakePrefsRepo(None), "owner/repo")
    assert identity == default_git_identity()


async def test_full_override():
    prefs = _FakePrefsRepo({"git_author_name": "Raven Bot", "git_author_email": "bot@raven-cargo.com"})
    identity = await resolve_git_identity(prefs, "owner/repo")
    assert identity == GitIdentity(name="Raven Bot", email="bot@raven-cargo.com")


async def test_partial_override_email_only():
    prefs = _FakePrefsRepo({"git_author_name": None, "git_author_email": "bot@raven-cargo.com"})
    identity = await resolve_git_identity(prefs, "owner/repo")
    assert identity.name == GIT_AUTHOR_NAME
    assert identity.email == "bot@raven-cargo.com"


async def test_fetch_failure_falls_back_to_default():
    identity = await resolve_git_identity(_FakePrefsRepo(raises=True), "owner/repo")
    assert identity == default_git_identity()


async def test_non_string_and_blank_values_degrade_to_default():
    prefs = _FakePrefsRepo({"git_author_name": 42, "git_author_email": "   "})
    identity = await resolve_git_identity(prefs, "owner/repo")
    assert identity == default_git_identity()


async def test_empty_repo_skips_lookup():
    prefs = _FakePrefsRepo({"git_author_email": "bot@raven-cargo.com"})
    identity = await resolve_git_identity(prefs, "")
    assert identity == default_git_identity()


def test_build_cmd_quotes_values():
    cmd = build_git_identity_cmd(GitIdentity(name="Raven Bot", email="bot@raven-cargo.com"))
    assert cmd == ("git config --global user.email bot@raven-cargo.com && git config --global user.name 'Raven Bot'")


def test_build_cmd_defends_against_shell_metacharacters():
    cmd = build_git_identity_cmd(GitIdentity(name="a; rm -rf /", email="x&&y@example.com"))
    assert "'a; rm -rf /'" in cmd
    assert "'x&&y@example.com'" in cmd
