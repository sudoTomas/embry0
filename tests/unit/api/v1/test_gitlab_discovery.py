"""GET /gitlab/discoverable-projects (#169)."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import embry0.api.v1.gitlab as gitlab_mod
from embry0.api.app import create_app
from embry0.config import Embry0Config
from tests.unit.api.v1.test_credentials import FakeSecrets, MemRepo


@pytest.fixture(autouse=True)
def _clear_cache():
    gitlab_mod._CACHE["at"] = 0.0
    gitlab_mod._CACHE["by_path"] = {}
    if "failed" in gitlab_mod._CACHE:
        gitlab_mod._CACHE["failed"] = []
    yield
    gitlab_mod._CACHE["at"] = 0.0
    gitlab_mod._CACHE["by_path"] = {}
    if "failed" in gitlab_mod._CACHE:
        gitlab_mod._CACHE["failed"] = []


def _project(path: str, **over):
    return {
        "path_with_namespace": path,
        "description": None,
        "visibility": "private",
        "web_url": f"https://gitlab.com/{path}",
        "default_branch": "main",
        "open_issues_count": 0,
        **over,
    }


def _app(*, gitlab_token: str = "glpat-x", registered: list[str] | None = None):
    config = Embry0Config(_env_file=None, auth_dev_mode=True, gitlab_token=gitlab_token)
    app = create_app(config)
    app.state.config = config
    repos_repo = MagicMock()
    repos_repo.list_all = AsyncMock(return_value=[{"repo": r} for r in (registered or [])])
    app.state.repos_repo = repos_repo
    return app


def _client(app):
    # An exception the app does not handle is answered as the 500 a caller gets, not raised into the test.
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")


async def test_reports_unconfigured_without_a_token():
    """A GitHub-only deployment is normal — not an error the UI should show."""
    async with _client(_app(gitlab_token="")) as c:
        body = (await c.get("/api/v1/gitlab/discoverable-projects")).json()
    assert body == {"projects": [], "cached": False, "configured": False, "failed": []}


async def test_lists_projects_and_excludes_registered_ones(monkeypatch):
    async def fake_paginate(self, path, **kwargs):
        return [_project("acme/products/fbr"), _project("acme/products/fbt")]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)

    async with _client(_app(registered=["acme/products/fbt"])) as c:
        body = (await c.get("/api/v1/gitlab/discoverable-projects")).json()

    assert body["configured"] is True
    assert [p["full_name"] for p in body["projects"]] == ["acme/products/fbr"]


async def test_nested_subgroup_paths_survive_intact(monkeypatch):
    async def fake_paginate(self, path, **kwargs):
        return [_project("acme/products/fbr")]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    async with _client(_app()) as c:
        body = (await c.get("/api/v1/gitlab/discoverable-projects")).json()
    assert body["projects"][0]["full_name"] == "acme/products/fbr"


async def test_only_public_visibility_is_reported_as_not_private(monkeypatch):
    """GitLab has three visibilities; `internal` is not public."""

    async def fake_paginate(self, path, **kwargs):
        return [
            _project("g/pub", visibility="public"),
            _project("g/internal", visibility="internal"),
            _project("g/priv", visibility="private"),
        ]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    async with _client(_app()) as c:
        body = (await c.get("/api/v1/gitlab/discoverable-projects")).json()
    by_name = {p["full_name"]: p["private"] for p in body["projects"]}
    assert by_name == {"g/pub": False, "g/internal": True, "g/priv": True}


async def test_default_branch_is_carried_through(monkeypatch):
    """fbt's is FBT_Source — surfacing it stops an operator assuming main."""

    async def fake_paginate(self, path, **kwargs):
        return [_project("acme/products/fbt", default_branch="FBT_Source")]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    async with _client(_app()) as c:
        body = (await c.get("/api/v1/gitlab/discoverable-projects")).json()
    assert body["projects"][0]["default_branch"] == "FBT_Source"


async def test_second_call_is_served_from_cache(monkeypatch):
    calls = {"n": 0}

    async def fake_paginate(self, path, **kwargs):
        calls["n"] += 1
        return [_project("g/p")]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    async with _client(_app()) as c:
        first = (await c.get("/api/v1/gitlab/discoverable-projects")).json()
        second = (await c.get("/api/v1/gitlab/discoverable-projects")).json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert calls["n"] == 1


async def test_refresh_bypasses_the_cache(monkeypatch):
    calls = {"n": 0}

    async def fake_paginate(self, path, **kwargs):
        calls["n"] += 1
        return [_project("g/p")]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    async with _client(_app()) as c:
        await c.get("/api/v1/gitlab/discoverable-projects")
        body = (await c.get("/api/v1/gitlab/discoverable-projects", params={"refresh": True})).json()
    assert body["cached"] is False
    assert calls["n"] == 2


async def test_malformed_entries_are_skipped(monkeypatch):
    async def fake_paginate(self, path, **kwargs):
        return [_project("g/good"), {"no_path": True}, "not-a-dict"]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    async with _client(_app()) as c:
        body = (await c.get("/api/v1/gitlab/discoverable-projects")).json()
    assert [p["full_name"] for p in body["projects"]] == ["g/good"]


async def test_membership_filter_is_requested(monkeypatch):
    seen: dict = {}

    async def fake_paginate(self, path, **kwargs):
        seen["path"] = path
        seen["params"] = kwargs.get("params")
        return []

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    async with _client(_app()) as c:
        await c.get("/api/v1/gitlab/discoverable-projects")
    assert seen["path"] == "/projects"
    assert seen["params"]["membership"] == "true"


async def test_transport_failure_surfaces_rather_than_lying(monkeypatch):
    """No account reachable: an error, not an empty list (#340 made it a 502 rather than an unhandled 500)."""

    async def boom(self, path, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", boom)
    async with _client(_app()) as c:
        resp = await c.get("/api/v1/gitlab/discoverable-projects")
    assert (resp.status_code, resp.json().get("detail")) == (502, "GitLab could not be reached"), resp.text


async def _add_gitlab_credential(app, label: str, secret: str) -> None:
    await app.state.credentials_repo.create(
        kind="forge_token",
        provider="gitlab",
        host="gitlab.example.com",
        label=label,
        secret_ciphertext="enc:" + secret,
        masked_preview="glpat-••••",
        created_by="test",
    )


def _with_registry(app):
    app.state.credentials_repo = MemRepo()
    app.state.secrets_provider = FakeSecrets()
    return app


async def test_registry_credential_counts_as_configured(monkeypatch):
    """A registry-only GitLab credential still enables discovery.

    ``configured`` asks the resolver. The listing client gets that credential's
    token and no resolver — the entry is the token.
    """
    seen: dict = {}

    async def fake_paginate(self, path, **kwargs):
        seen["resolver"] = self._credential_resolver
        seen["token"] = self._default_token
        return [_project("g/p")]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    app = _with_registry(_app(gitlab_token=""))
    await _add_gitlab_credential(app, "alice", "glpat-alice-placeholder")
    resolver = MagicMock()
    resolver.has_forge_credential = AsyncMock(return_value=True)
    app.state.credential_resolver = resolver
    async with _client(app) as c:
        resp = await c.get("/api/v1/gitlab/discoverable-projects")
    body = resp.json()
    assert body["configured"] is True, body
    assert [p["full_name"] for p in body["projects"]] == ["g/p"]
    assert body["projects"][0]["credential_id"] == "cred-000000000001"
    assert body["projects"][0]["credential_label"] == "alice"
    assert seen["resolver"] is None
    assert seen["token"] == "glpat-alice-placeholder"
    assert "glpat-alice-placeholder" not in resp.text
    assert "glpat-alice-placeholder" not in str(gitlab_mod._CACHE)
    resolver.has_forge_credential.assert_awaited_once_with("gitlab")


async def test_discoverable_projects_carry_credential_id(monkeypatch):
    async def fake_paginate(self, path, **kwargs):
        if self._default_token == "glpat-alice-placeholder":
            return [_project("acme/widgets"), _project("acme/shared")]
        if self._default_token == "glpat-env-placeholder":
            return [_project("acme/gadgets"), _project("acme/shared")]
        return []

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    app = _with_registry(_app(gitlab_token="glpat-env-placeholder"))
    await _add_gitlab_credential(app, "alice", "glpat-alice-placeholder")
    async with _client(app) as c:
        resp = await c.get("/api/v1/gitlab/discoverable-projects")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_name = {p["full_name"]: p for p in body["projects"]}
    assert by_name["acme/widgets"]["credential_id"] == "cred-000000000001"
    assert by_name["acme/widgets"]["credential_label"] == "alice"
    assert by_name["acme/gadgets"]["credential_id"] is None
    assert by_name["acme/gadgets"]["credential_label"] == "GITLAB_TOKEN"
    # Registry is listed first, so the shared path keeps alice.
    assert by_name["acme/shared"]["credential_id"] == "cred-000000000001"
    assert body["failed"] == []
    assert "glpat-alice-placeholder" not in resp.text
    assert "glpat-env-placeholder" not in resp.text
    assert "glpat-alice-placeholder" not in str(gitlab_mod._CACHE)
    assert "glpat-env-placeholder" not in str(gitlab_mod._CACHE)


async def test_one_failing_gitlab_credential_is_reported_by_label(monkeypatch):
    async def fake_paginate(self, path, **kwargs):
        if self._default_token == "glpat-revoked-placeholder":
            request = httpx.Request("GET", "https://gitlab.example.com/api/v4/projects")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401", request=request, response=response)
        return [_project("acme/widgets")]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    app = _with_registry(_app(gitlab_token=""))
    await _add_gitlab_credential(app, "alice", "glpat-alice-placeholder")
    await _add_gitlab_credential(app, "revoked", "glpat-revoked-placeholder")
    resolver = MagicMock()
    resolver.has_forge_credential = AsyncMock(return_value=True)
    app.state.credential_resolver = resolver
    async with _client(app) as c:
        resp = await c.get("/api/v1/gitlab/discoverable-projects")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["failed"] == ["revoked"]
    assert "acme/widgets" in {p["full_name"] for p in body["projects"]}
    assert "glpat-revoked-placeholder" not in resp.text
    assert "glpat-alice-placeholder" not in resp.text
    assert "glpat-revoked-placeholder" not in str(gitlab_mod._CACHE)
    assert "glpat-alice-placeholder" not in str(gitlab_mod._CACHE)


async def test_a_gitlab_account_that_never_answers_is_reported_by_label(monkeypatch):
    """#340, the GitLab listing alike: one account's transport error failed every account's listing."""

    async def fake_paginate(self, path, **kwargs):
        if self._default_token == "glpat-offline-placeholder":
            raise httpx.ReadTimeout("timed out")
        return [_project("acme/widgets")]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    app = _with_registry(_app(gitlab_token=""))
    await _add_gitlab_credential(app, "alice", "glpat-alice-placeholder")
    await _add_gitlab_credential(app, "offline", "glpat-offline-placeholder")
    resolver = MagicMock()
    resolver.has_forge_credential = AsyncMock(return_value=True)
    app.state.credential_resolver = resolver
    async with _client(app) as c:
        resp = await c.get("/api/v1/gitlab/discoverable-projects")
    assert resp.status_code == 200, f"one account's timeout fails the whole listing: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["failed"] == ["offline"], f"the account that could not be listed is named: {body['failed']}"
    assert [p["full_name"] for p in body["projects"]] == ["acme/widgets"], body["projects"]


async def test_gitlab_refusing_every_account_is_a_502_naming_its_status(monkeypatch):
    """#568, GitLab alike: its 401 was answered as embry0's own, a bad embry0 key to the dashboard."""

    async def refused(self, path, **kwargs):
        request = httpx.Request("GET", "https://gitlab.example.com/api/v4/projects")
        raise httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", refused)
    async with _client(_app()) as c:
        resp = await c.get("/api/v1/gitlab/discoverable-projects")
    assert (resp.status_code, resp.json().get("detail")) == (502, "GitLab API returned 401 for every account"), (
        f"GitLab refusing every account is answered as an upstream failure, not the caller's: {resp.text}"
    )


def _short_deadline(monkeypatch) -> None:
    """The listing's deadline, a fraction of a second here. A tree without one has nothing to shorten, and the test
    then shows that it waits on the hanging accounts."""
    try:
        import embry0.api.forge_listings as forge_listings
    except ImportError:
        return
    monkeypatch.setattr(forge_listings, "DEADLINE_SECONDS", 0.2)


async def test_hanging_gitlab_accounts_end_at_the_deadline(monkeypatch):
    """#340 review, GitLab alike: the accounts are read at once, within one deadline."""
    import asyncio
    import time

    async def fake_paginate(self, path, **kwargs):
        if self._default_token.startswith("glpat-slow"):
            await asyncio.Event().wait()
        return [_project("acme/widgets")]

    _short_deadline(monkeypatch)
    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    app = _with_registry(_app(gitlab_token=""))
    await _add_gitlab_credential(app, "alice", "glpat-alice-placeholder")
    for n in range(4):
        await _add_gitlab_credential(app, f"slow-{n}", f"glpat-slow{n}-placeholder")
    resolver = MagicMock()
    resolver.has_forge_credential = AsyncMock(return_value=True)
    app.state.credential_resolver = resolver
    started = time.monotonic()
    try:
        async with _client(app) as c:
            resp = await asyncio.wait_for(c.get("/api/v1/gitlab/discoverable-projects"), timeout=5)
    except TimeoutError:
        pytest.fail("the listing waited on the hanging accounts past 5 s instead of its deadline")
    took = time.monotonic() - started
    assert resp.status_code == 200 and took < 3, f"answered at the deadline: {resp.status_code} after {took:.2f}s"
    body = resp.json()
    assert sorted(body["failed"]) == [f"slow-{n}" for n in range(4)], f"each hanging account is named: {body}"
    assert [p["full_name"] for p in body["projects"]] == ["acme/widgets"], body["projects"]


async def test_a_gitlab_answer_missing_an_unreachable_account_is_not_kept(monkeypatch):
    """#340 review, GitLab alike: an answer without an account that could not be reached is served once, not kept."""
    down = {"glpat-offline-placeholder"}

    async def fake_paginate(self, path, **kwargs):
        if self._default_token in down:
            raise httpx.ConnectError("connection refused")
        return [_project("acme/widgets" if self._default_token == "glpat-alice-placeholder" else "acme/gadgets")]

    monkeypatch.setattr("embry0.execution.forge_client.ForgeClient.paginate", fake_paginate)
    app = _with_registry(_app(gitlab_token=""))
    await _add_gitlab_credential(app, "alice", "glpat-alice-placeholder")
    await _add_gitlab_credential(app, "offline", "glpat-offline-placeholder")
    resolver = MagicMock()
    resolver.has_forge_credential = AsyncMock(return_value=True)
    app.state.credential_resolver = resolver
    async with _client(app) as c:
        first = (await c.get("/api/v1/gitlab/discoverable-projects")).json()
        down.clear()
        second = (await c.get("/api/v1/gitlab/discoverable-projects")).json()
    assert first["failed"] == ["offline"], first
    assert (second["cached"], second["failed"], sorted(p["full_name"] for p in second["projects"])) == (
        False,
        [],
        ["acme/gadgets", "acme/widgets"],
    ), f"the next read asks the account that was missing: {second}"
