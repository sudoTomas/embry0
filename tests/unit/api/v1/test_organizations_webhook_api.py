"""PATCH /orgs/{owner} and the organization webhook routes (ADR 0012)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import organizations as organizations_api
from embry0.config import Embry0Config
from embry0.services.organizations import Organization

TARGET = "https://hooks.example.com/api/v1/webhook"


class FakeOrgsRepo:
    def __init__(self) -> None:
        self.orgs: dict[str, Organization] = {}

    async def get(self, owner: str) -> Organization | None:
        return self.orgs.get(owner.lower())

    async def patch(
        self, owner: str, *, display_name: str | None = None, settings: dict[str, Any] | None = None
    ) -> Organization | None:
        org = self.orgs.get(owner.lower())
        if org is None:
            return None
        if display_name is not None:
            org.display_name = display_name
        org.settings = {**org.settings, **(settings or {})}
        return org


class FakeResponse:
    def __init__(self, status_code: int, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class FakeForge:
    """GitHub's organization hooks API, as far as the routes use it."""

    def __init__(self, *, visible: bool = True, create_status: int = 201) -> None:
        self.hooks: list[dict[str, Any]] = []
        self.visible = visible
        self.create_status = create_status
        self.posts: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, **kwargs: Any) -> FakeResponse:
        assert path == "/orgs/acme/hooks", path
        return FakeResponse(200, list(self.hooks)) if self.visible else FakeResponse(404, {"message": "Not Found"})

    async def post(self, path: str, **kwargs: Any) -> FakeResponse:
        self.posts.append((path, kwargs["json"]))
        if self.create_status != 201:
            return FakeResponse(self.create_status, {"message": "Resource not accessible by integration"})
        hook = {
            "id": 100 + len(self.hooks),
            "active": kwargs["json"]["active"],
            "events": kwargs["json"]["events"],
            "config": {"url": kwargs["json"]["config"]["url"], "content_type": "json", "insecure_ssl": "0"},
        }
        self.hooks.append(hook)
        return FakeResponse(201, hook)


@pytest.fixture
def forge() -> FakeForge:
    return FakeForge()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, forge: FakeForge) -> Any:
    config = Embry0Config(
        _env_file=None,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        github_token="env-token",
        github_webhook_secret="hook-secret",
        webhook_public_url="https://hooks.example.com/",
    )
    app = create_app(config)
    app.state.organizations_repo = FakeOrgsRepo()
    app.state.db = None
    app.state.organizations_repo.orgs["acme"] = Organization(
        owner="acme", forge="github", access={"owner_kind": "organization", "hooks": True, "read": True}
    )
    tokens: list[str] = []

    def fake_client(request: Any, org: Organization, token: str) -> FakeForge:
        tokens.append(token)
        return forge

    monkeypatch.setattr(organizations_api, "make_forge_client", fake_client)
    app.state.tokens = tokens
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    )


# ---- PATCH /orgs/{owner} ----


@pytest.mark.asyncio
async def test_patch_sets_auto_register_and_merges_settings(app: Any) -> None:
    app.state.organizations_repo.orgs["acme"].settings = {"tracker": "github"}
    async with _client(app) as client:
        resp = await client.patch("/api/v1/orgs/acme", json={"settings": {"auto_register": True}})
        named = await client.patch("/api/v1/orgs/acme", json={"display_name": "Acme Inc"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["settings"] == {"tracker": "github", "auto_register": True}
    assert named.json()["display_name"] == "Acme Inc"
    assert named.json()["settings"]["auto_register"] is True


@pytest.mark.asyncio
async def test_patch_refuses_unknown_keys_and_wrong_types(app: Any) -> None:
    async with _client(app) as client:
        unknown = await client.patch("/api/v1/orgs/acme", json={"settings": {"auto_regster": True}})
        typed = await client.patch("/api/v1/orgs/acme", json={"settings": {"auto_register": "yes"}})
        missing = await client.patch("/api/v1/orgs/nobody", json={"settings": {"auto_register": True}})
    assert unknown.status_code == 422 and "unknown setting 'auto_regster'" in unknown.json()["detail"]
    assert typed.status_code == 422 and "must be bool" in typed.json()["detail"]
    assert missing.status_code == 404
    assert app.state.organizations_repo.orgs["acme"].settings == {}


@pytest.mark.asyncio
async def test_patch_sets_and_clears_the_rediscovery_interval(app: Any) -> None:
    """ADR 0016: an integer from 1 to 168 puts the owner on the schedule, null takes it off."""
    async with _client(app) as client:
        on = await client.patch("/api/v1/orgs/acme", json={"settings": {"rediscover_hours": 24}})
        off = await client.patch("/api/v1/orgs/acme", json={"settings": {"rediscover_hours": None}})
        refused = [
            await client.patch("/api/v1/orgs/acme", json={"settings": {"rediscover_hours": value}})
            for value in (0, 169, True, 1.5, "24")
        ]
    assert on.status_code == 200, on.text
    assert on.json()["settings"] == {"rediscover_hours": 24}
    assert off.status_code == 200 and off.json()["settings"] == {"rediscover_hours": None}
    assert [r.status_code for r in refused] == [422] * 5
    assert "must be from 1 to 168 or null" in refused[0].json()["detail"]
    assert "must be int or null" in refused[2].json()["detail"]
    assert app.state.organizations_repo.orgs["acme"].settings == {"rediscover_hours": None}


@pytest.mark.asyncio
async def test_patch_sets_and_clears_the_context_audit_interval(app: Any) -> None:
    """ADR 0020: a whole number of days from 1 to 365 puts the owner on the schedule, null takes it off."""
    async with _client(app) as client:
        on = await client.patch("/api/v1/orgs/acme", json={"settings": {"context_audit_days": 7}})
        off = await client.patch("/api/v1/orgs/acme", json={"settings": {"context_audit_days": None}})
        refused = [
            await client.patch("/api/v1/orgs/acme", json={"settings": {"context_audit_days": value}})
            for value in (0, 366, True, 1.5, "7")
        ]
    assert on.status_code == 200, on.text
    assert on.json()["settings"] == {"context_audit_days": 7}
    assert off.status_code == 200 and off.json()["settings"] == {"context_audit_days": None}
    assert [r.status_code for r in refused] == [422] * 5
    assert "must be from 1 to 365 or null" in refused[0].json()["detail"]
    assert "must be int or null" in refused[2].json()["detail"]
    assert app.state.organizations_repo.orgs["acme"].settings == {"context_audit_days": None}


class _Layers:
    """The settings store as PATCH /orgs writes a moved setting into an organization's layer."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, dict[str, Any]]] = []

    async def set_keys(self, section: str, keys: dict[str, Any], **kw: Any) -> Any:
        self.writes.append((str(kw["scope"]), section, dict(keys)))
        return SimpleNamespace(revision_id=None)


@pytest.mark.asyncio
async def test_patch_sets_and_clears_the_context_audit_caps(app: Any) -> None:
    """An organization's own caps for the context audit, in its layer of the budget section now (#171); null
    hands them back to the deployment. The old route checks them as it did, for the release it stays."""
    app.state.settings = layers = _Layers()
    async with _client(app) as client:
        on = await client.patch(
            "/api/v1/orgs/acme",
            json={"settings": {"context_audit_budget_usd": 5, "context_audit_max_jobs_per_tick": 0}},
        )
        off = await client.patch(
            "/api/v1/orgs/acme",
            json={"settings": {"context_audit_budget_usd": None, "context_audit_max_jobs_per_tick": None}},
        )
        refused = [
            await client.patch("/api/v1/orgs/acme", json={"settings": settings})
            for settings in (
                {"context_audit_budget_usd": 0},
                {"context_audit_budget_usd": 51},
                {"context_audit_budget_usd": "5"},
                {"context_audit_max_jobs_per_tick": 51},
                {"context_audit_max_jobs_per_tick": 1.5},
            )
        ]
    assert on.status_code == 200, on.text
    assert [note["moved_to"] for note in on.json()["deprecated"]] == [
        "budget.context_audit_job_usd",
        "budget.context_audit_max_jobs_per_tick",
    ]
    assert off.status_code == 200
    assert layers.writes == [
        ("organization:acme", "budget", {"context_audit_job_usd": 5.0, "context_audit_max_jobs_per_tick": 0}),
        ("organization:acme", "budget", {"context_audit_job_usd": None, "context_audit_max_jobs_per_tick": None}),
    ]
    assert app.state.organizations_repo.orgs["acme"].settings == {}, "a moved setting is not stored here any more"
    assert [r.status_code for r in refused] == [422] * 5
    assert "must be more than 0 and at most 50 or null" in refused[0].json()["detail"]
    assert "must be a number or null" in refused[2].json()["detail"]
    assert "must be from 0 to 50 or null" in refused[3].json()["detail"]
    assert "must be int or null" in refused[4].json()["detail"]


@pytest.mark.asyncio
async def test_patch_refuses_a_setting_outside_settings(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.patch("/api/v1/orgs/acme", json={"auto_register": True})
    assert resp.status_code == 422
    assert app.state.organizations_repo.orgs["acme"].settings == {}


# ---- the organization webhook ----


@pytest.mark.asyncio
async def test_install_creates_the_hook_once_and_then_answers_already(app: Any, forge: FakeForge) -> None:
    async with _client(app) as client:
        first = await client.post("/api/v1/orgs/acme/webhook")
        second = await client.post("/api/v1/orgs/acme/webhook")
    assert first.status_code == 200, first.text
    assert first.json()["outcome"] == "done"
    assert first.json()["target_url"] == TARGET
    assert first.json()["hook"]["url"] == TARGET
    assert first.json()["hook"]["events"] == ["issues", "issue_comment", "pull_request", "workflow_run"]
    assert second.json()["outcome"] == "already"
    assert second.json()["hook"]["id"] == first.json()["hook"]["id"]
    assert len(forge.posts) == 1
    path, body = forge.posts[0]
    assert path == "/orgs/acme/hooks"
    assert body["config"] == {"url": TARGET, "content_type": "json", "secret": "hook-secret", "insecure_ssl": "0"}
    assert app.state.tokens == ["env-token", "env-token"]


@pytest.mark.asyncio
async def test_the_response_never_carries_the_secret(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/webhook")
        shown = await client.get("/api/v1/orgs/acme/webhook")
    assert "hook-secret" not in resp.text
    assert "hook-secret" not in shown.text


@pytest.mark.asyncio
async def test_get_reports_whether_a_hook_points_here(app: Any, forge: FakeForge) -> None:
    forge.hooks.append(
        {"id": 5, "active": True, "events": ["push"], "config": {"url": "https://elsewhere.example.com/x"}}
    )
    async with _client(app) as client:
        before = await client.get("/api/v1/orgs/acme/webhook")
        await client.post("/api/v1/orgs/acme/webhook")
        after = await client.get("/api/v1/orgs/acme/webhook")
    assert before.status_code == 200, before.text
    assert before.json()["installed"] is False
    assert before.json()["hook"] is None
    assert before.json()["target_url"] == TARGET
    assert before.json()["hooks_visible"] is True
    assert [h["url"] for h in before.json()["hooks"]] == ["https://elsewhere.example.com"]
    assert after.json()["installed"] is True
    assert after.json()["hook"]["url"] == TARGET
    assert [h["url"] for h in after.json()["hooks"]] == ["https://elsewhere.example.com", TARGET]


@pytest.mark.asyncio
async def test_get_shows_a_foreign_hook_by_its_origin_only(app: Any, forge: FakeForge) -> None:
    forge.hooks.append(
        {
            "id": 6,
            "active": True,
            "events": ["push"],
            "config": {"url": "https://bot:pw-123@chat.example.com:8443/services/T0/B0/tok-456?key=k-789"},
        }
    )
    async with _client(app) as client:
        resp = await client.get("/api/v1/orgs/acme/webhook")
    assert resp.json()["hooks"] == [
        {"id": 6, "url": "https://chat.example.com:8443", "events": ["push"], "active": True}
    ]
    for secret in ("pw-123", "tok-456", "k-789", "bot"):
        assert secret not in resp.text


@pytest.mark.asyncio
async def test_get_says_when_hooks_are_not_visible(app: Any, forge: FakeForge) -> None:
    forge.visible = False
    async with _client(app) as client:
        resp = await client.get("/api/v1/orgs/acme/webhook")
    assert resp.json() == {
        "owner": "acme",
        "installed": False,
        "hook": None,
        "target_url": TARGET,
        "hooks_visible": False,
        "hooks": [],
    }


@pytest.mark.asyncio
async def test_install_is_refused_when_the_probe_found_no_hook_access(app: Any, forge: FakeForge) -> None:
    app.state.organizations_repo.orgs["acme"].access["hooks"] = False
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/webhook")
    assert resp.status_code == 422
    assert "hook administration" in resp.json()["detail"]
    assert forge.posts == []


@pytest.mark.asyncio
async def test_install_is_refused_for_a_user_owner_a_paused_org_and_gitlab(app: Any, forge: FakeForge) -> None:
    orgs = app.state.organizations_repo.orgs
    orgs["octocat"] = Organization(owner="octocat", forge="github", access={"owner_kind": "user", "hooks": True})
    orgs["paused"] = Organization(owner="paused", forge="github", status="paused", access={"hooks": True})
    orgs["group"] = Organization(owner="group", forge="gitlab", access={"read": True})
    async with _client(app) as client:
        user = await client.post("/api/v1/orgs/octocat/webhook")
        paused = await client.post("/api/v1/orgs/paused/webhook")
        gitlab = await client.post("/api/v1/orgs/group/webhook")
        missing = await client.post("/api/v1/orgs/nobody/webhook")
    assert user.status_code == 422 and "user account" in user.json()["detail"]
    assert paused.status_code == 409
    assert gitlab.status_code == 422 and "GitHub" in gitlab.json()["detail"]
    assert missing.status_code == 404
    assert forge.posts == []


@pytest.mark.asyncio
async def test_install_needs_a_public_origin_and_a_secret(app: Any, forge: FakeForge) -> None:
    base = dict(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="env-token")
    app.state.config = Embry0Config(**base, github_webhook_secret="hook-secret")
    async with _client(app) as client:
        no_origin = await client.post("/api/v1/orgs/acme/webhook")
        shown = await client.get("/api/v1/orgs/acme/webhook")
    app.state.config = Embry0Config(**base, webhook_public_url="https://hooks.example.com", github_webhook_secret="")
    async with _client(app) as client:
        no_secret = await client.post("/api/v1/orgs/acme/webhook")
    assert no_origin.status_code == 422 and "WEBHOOK_PUBLIC_URL" in no_origin.json()["detail"]
    assert shown.json()["installed"] is False and shown.json()["target_url"] is None
    assert no_secret.status_code == 422 and "GITHUB_WEBHOOK_SECRET" in no_secret.json()["detail"]
    assert forge.posts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8200", "http://localhost:8200/", "http://[::1]:8200", "http://0.0.0.0"]
)
async def test_install_refuses_a_loopback_webhook_origin(app: Any, forge: FakeForge, url: str) -> None:
    """No forge can deliver to a loopback address, so an organization hook pointing there is refused, as the
    organization hook action skips it."""
    app.state.config = Embry0Config(
        _env_file=None,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        github_token="env-token",
        github_webhook_secret="hook-secret",
        webhook_public_url=url,
    )
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/webhook")
    assert resp.status_code == 422, (
        f"a loopback WEBHOOK_PUBLIC_URL ({url}) was accepted: {resp.status_code} {resp.text}"
    )
    assert "WEBHOOK_PUBLIC_URL" in resp.json()["detail"]
    assert forge.posts == [], "an organization hook was created pointing at a loopback address"


@pytest.mark.asyncio
async def test_install_reports_the_forge_refusing(app: Any, forge: FakeForge) -> None:
    forge.create_status = 403
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/webhook")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "the forge answered 403: Resource not accessible by integration"


@pytest.mark.asyncio
async def test_install_reports_hooks_the_credential_cannot_list(app: Any, forge: FakeForge) -> None:
    forge.visible = False
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/webhook")
    assert resp.status_code == 502
    assert "cannot list the webhooks of acme" in resp.json()["detail"]
    assert forge.posts == []
