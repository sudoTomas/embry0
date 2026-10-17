"""The access probe learns what a credential can do at an owner by calling the forge."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services.organizations import probe_github, probe_gitlab


class _Resp:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeForge:
    """Answers by path; records what was asked so a test can prove the probe never read `permissions`."""

    def __init__(self, routes: dict[str, _Resp]) -> None:
        self.routes = routes
        self.asked: list[str] = []

    async def get(self, path: str, **_: Any) -> _Resp:
        self.asked.append(path)
        return self.routes.get(path, _Resp(404))


@pytest.mark.asyncio
async def test_github_org_admin_token_reads_and_manages_hooks() -> None:
    forge = FakeForge(
        {
            "/user": _Resp(200, {"login": "ops-bot"}),
            "/orgs/acme": _Resp(200, {"login": "acme", "public_repos": 3, "total_private_repos": 9}),
            "/orgs/acme/repos": _Resp(200, [{"full_name": "acme/backend"}]),
            "/repos/acme/backend/hooks": _Resp(200, []),
        }
    )
    probe = await probe_github("acme", "tok", client=forge)
    assert probe.identity == "ops-bot"
    assert probe.owner_kind == "organization"
    assert probe.read is True
    assert probe.hooks is True
    assert probe.push is None
    assert probe.repo_count == 12
    assert probe.sample_repo == "acme/backend"
    assert "can manage webhooks" in probe.detail
    assert not any("permission" in p for p in forge.asked)


@pytest.mark.asyncio
async def test_github_token_without_hook_grant_is_read_only() -> None:
    forge = FakeForge(
        {
            "/user": _Resp(200, {"login": "scoped"}),
            "/orgs/acme": _Resp(404),
            "/users/acme": _Resp(200, {"login": "acme", "public_repos": 2}),
            "/users/acme/repos": _Resp(200, [{"full_name": "acme/site"}]),
            "/repos/acme/site/hooks": _Resp(403),
        }
    )
    probe = await probe_github("acme", "tok", client=forge)
    assert probe.owner_kind == "user"
    assert probe.read is True
    assert probe.hooks is False
    assert "cannot manage webhooks" in probe.detail


@pytest.mark.asyncio
async def test_github_owner_the_token_cannot_see_is_not_readable() -> None:
    forge = FakeForge({"/user": _Resp(200, {"login": "someone"})})
    probe = await probe_github("ghost", "tok", client=forge)
    assert probe.owner_kind is None
    assert probe.read is False
    assert probe.hooks is None
    assert probe.sample_repo is None
    assert "not visible" in probe.detail


@pytest.mark.asyncio
async def test_gitlab_group_read_leaves_hooks_unprobed() -> None:
    forge = FakeForge(
        {
            "/user": _Resp(200, {"username": "bot"}),
            "/groups/acme": _Resp(200, {"id": 7}),
            "/groups/acme/projects": _Resp(200, [{"path_with_namespace": "acme/api"}]),
        }
    )
    probe = await probe_gitlab("acme", "tok", gitlab_url="https://gitlab.example", client=forge)
    assert probe.read is True
    assert probe.hooks is None
    assert probe.sample_repo == "acme/api"
    assert probe.owner_kind == "group"
