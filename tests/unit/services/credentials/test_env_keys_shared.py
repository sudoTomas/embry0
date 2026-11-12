"""An owner's env token serves none of the owners that share its variable's name.

An env variable cannot spell '-' or '.': ``GITLAB_TOKEN__MY_GROUP`` is read by ``my-group``,
``my.group`` and ``my_group``. When another registered owner reads the variable a repository's owner
reads, the resolver serves it to neither, logs ``credential_env_key_shared`` and reads the forge's
default variable instead. One owner reading it, registered or not, is served as before.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.api.v1 import organizations
from embry0.services.credentials.resolver import CredentialResolver
from tests.unit.services.credentials.test_resolver import FakeRepo, FakeSecrets, _cfg


def _resolver(known: dict[str, set[str]], env: dict[str, str], *, broken: bool = False) -> CredentialResolver:
    repo = FakeRepo()
    repo.known = known
    if broken:

        async def unreadable() -> Any:
            raise RuntimeError("registry down")

        repo.known_owners = unreadable  # type: ignore[method-assign]
    return CredentialResolver(repo, FakeSecrets(), _cfg(github_token="tok-gh", gitlab_token="tok-gl"), environ=env)


async def _served(resolver: CredentialResolver, full_repo: str, forge: str) -> tuple[str, str, tuple[str, ...]]:
    got = await resolver.resolve_forge(full_repo, forge)
    return got.secret, got.label, got.trace


async def test_a_group_variable_two_registered_groups_read_serves_neither() -> None:
    env = {"GITLAB_TOKEN__MY_GROUP": "tok-my-group"}
    resolver = _resolver({"gitlab": {"my-group", "my_group", "acme"}}, env)
    with capture_logs() as logs:
        got = {r: await _served(resolver, r, "gitlab") for r in ("my-group/app", "my_group/app")}
    assert {r: v[:2] for r, v in got.items()} == {
        "my-group/app": ("tok-gl", "GITLAB_TOKEN"),
        "my_group/app": ("tok-gl", "GITLAB_TOKEN"),
    }, f"GITLAB_TOKEN__MY_GROUP serves neither group two registered groups read it as: {got}"
    assert "env:GITLAB_TOKEN__MY_GROUP -> skipped (read by my-group, my_group)" in got["my-group/app"][2], got
    shared = [e for e in logs if e.get("event") == "credential_env_key_shared"]
    assert [(e["env_key"], e["owners"]) for e in shared] == [("GITLAB_TOKEN__MY_GROUP", ["my-group", "my_group"])] * 2


async def test_a_login_variable_two_registered_owners_read_serves_neither() -> None:
    env = {"GITHUB_TOKEN__MY_ORG": "tok-my-org"}
    got = await _served(_resolver({"github": {"my-org", "my_org"}}, env), "my-org/app", "github")
    assert got[:2] == ("tok-gh", "GITHUB_TOKEN"), f"GITHUB_TOKEN__MY_ORG serves neither owner reading it: {got}"


async def test_a_variable_one_owner_reads_serves_it_as_before() -> None:
    env = {"GITLAB_TOKEN__MY_GROUP": "tok-my-group", "GITHUB_TOKEN__ACME": "tok-acme"}
    registered = await _served(_resolver({"gitlab": {"my-group"}, "github": {"my_group"}}, env), "my-group/x", "gitlab")
    nobody = await _served(_resolver({}, env), "my-group/x", "gitlab")
    plain = await _served(_resolver({}, env, broken=True), "acme/x", "github")
    assert (registered[:2], nobody[:2], plain[:2]) == (
        ("tok-my-group", "GITLAB_TOKEN__MY_GROUP"),
        ("tok-my-group", "GITLAB_TOKEN__MY_GROUP"),
        ("tok-acme", "GITHUB_TOKEN__ACME"),
    ), (
        "one registered reader (another forge's my_group aside), none registered, and a name no other can share "
        f"(read with no lookup) are served as before: {registered}, {nobody}, {plain}"
    )


async def test_a_variable_a_registered_owner_shares_serves_no_unregistered_owner() -> None:
    """my.group is registered nowhere; my-group is. The variable cannot say which it is for."""
    env = {"GITLAB_TOKEN__MY_GROUP": "tok-my-group"}
    got = await _served(_resolver({"gitlab": {"my-group"}}, env), "my.group/app", "gitlab")
    assert got[:2] == ("tok-gl", "GITLAB_TOKEN"), f"my.group is not served my-group's variable: {got}"


async def test_unread_registered_owners_serve_no_variable_another_owner_could_share() -> None:
    env = {"GITLAB_TOKEN__MY_GROUP": "tok-my-group"}
    with capture_logs() as logs:
        got = await _served(_resolver({}, env, broken=True), "my-group/app", "gitlab")
    assert got[:2] == ("tok-gl", "GITLAB_TOKEN"), f"with the registered owners unread, the default serves: {got}"
    assert [e["env_key"] for e in logs if e.get("event") == "credential_env_key_unchecked"] == [
        "GITLAB_TOKEN__MY_GROUP"
    ]


async def test_an_organization_probe_reads_no_variable_another_owner_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    """The organization probe acts with the owner's env token as the resolver reads it."""
    env = {"GITHUB_TOKEN__MY_ORG": "tok-my-org"}
    monkeypatch.setenv("GITHUB_TOKEN__MY_ORG", "tok-my-org")
    tokens = {}
    for known in ({"github": {"my-org"}}, {"github": {"my-org", "my_org"}}):
        state = SimpleNamespace(config=_cfg(github_token="tok-gh"), credential_resolver=_resolver(known, env))
        request = SimpleNamespace(app=SimpleNamespace(state=state))
        token, _source = await organizations._token_for(request, "my-org", "github", None)  # noqa: SLF001
        tokens[len(known["github"])] = token
    assert tokens == {1: "tok-my-org", 2: "tok-gh"}, (
        f"the probe reads my-org's variable only while it is my-org's: {tokens}"
    )
