"""POST /orgs/{owner}/actions (ADR 0010): acting on the needs list through the API."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api import repo_allowlist
from embry0.api.app import create_app
from embry0.api.deps import bust_api_key_cache
from embry0.api.v1 import jobs as jobs_api
from embry0.api.v1 import org_actions as org_actions_api
from embry0.api.v1 import organizations as organizations_api
from embry0.api.v1 import watcher_stacks as watcher_stacks_api
from embry0.config import Embry0Config
from embry0.services import org_discovery
from embry0.services.organizations import Organization
from embry0.services.watcher_stacks import WatcherStack
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

HOOK_URL = "https://hooks.example.com/api/v1/webhook"

ADMIN_TOKEN = "emb_" + "a" * 43
LIMITED_ADMIN_TOKEN = "emb_" + "b" * 43
OPERATOR_TOKEN = "emb_" + "c" * 43


def _key(key_id: str, name: str, scope: str, allowed: tuple[str, ...] | None = None) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id=None,
        name=name,
        prefix="emb_xxxxxxxx",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=allowed,
    )


KEYS = {
    hash_token(ADMIN_TOKEN): _key("key-orgadmin0001", "org-admin", "admin"),
    hash_token(LIMITED_ADMIN_TOKEN): _key("key-orglimited01", "org-bot", "admin", ("acme/one",)),
    hash_token(OPERATOR_TOKEN): _key("key-orgoperator1", "org-operator", "operate"),
}


class FakeOrgsRepo:
    def __init__(self) -> None:
        self.orgs: dict[str, Organization] = {}

    async def get(self, owner: str) -> Organization | None:
        return self.orgs.get(owner.lower())

    async def mark_discovered(self, owner: str, *, repo_count: int, when: Any = None) -> Organization | None:
        return self.orgs.get(owner.lower())


class FakeFactsRepo:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []

    async def list_for_owner(self, owner: str, *, include_archived: bool = True) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["owner"] == owner.lower()]

    async def start_run(self, owner: str) -> dict[str, Any]:
        run = {"id": f"odr-{len(self.runs) + 1}", "owner": owner, "status": "running", "error": None}
        self.runs.append(run)
        return run

    async def running_run(self, owner: str) -> dict[str, Any] | None:
        return next((r for r in self.runs if r["owner"] == owner.lower() and r["status"] == "running"), None)

    async def progress(self, run_id: str, *, seen: int, total: int) -> None:
        return None

    async def upsert_fact(self, facts: Any, *, run_id: str) -> None:
        return None

    async def finish_run(
        self, run_id: str, *, summary: dict[str, Any] | None = None, error: str | None = None
    ) -> dict[str, Any] | None:
        for run in self.runs:
            if run["id"] == run_id:
                run.update(status="error" if error else "done", error=error)
                return run
        return None


class FakeRegistry:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def registered_as(self, repo: str) -> str | None:
        return next((r for r in self.rows if r.lower() == repo.lower()), None)

    async def register(self, repo: str, notes: str = "", forge: str = "") -> dict[str, Any]:
        self.rows.setdefault(repo, {"repo": repo, "notes": notes, "forge": forge, "active": True})
        return self.rows[repo]

    async def get(self, repo: str) -> dict[str, Any] | None:
        return self.rows.get(repo)

    async def set_active(self, repo: str, active: bool) -> dict[str, Any] | None:
        row = self.rows.get(repo)
        if row is not None:
            row["active"] = active
        return row


class FakeStacks:
    def __init__(self) -> None:
        self.rows: dict[str, WatcherStack] = {}

    async def get(self, repo: str) -> WatcherStack | None:
        return self.rows.get(repo)

    async def list_all(self, *, enabled_only: bool = False) -> list[WatcherStack]:
        return [self.rows[repo] for repo in sorted(self.rows)]

    async def upsert(self, stack: WatcherStack) -> WatcherStack:
        self.rows[stack.repo] = stack
        return stack


class FakeQa:
    async def get(self, repo: str) -> dict[str, Any] | None:
        return None


class FakeJobs:
    """Newest first, as JobsRepository.list_all lists them."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def list_all(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        rows = [
            r
            for r in reversed(self.rows)
            if kwargs.get("repo") in (None, r["repo"])
            and kwargs.get("pipeline_template") in (None, r.get("pipeline_template"))
        ]
        return rows[: kwargs.get("limit", 50)], len(rows)


class FakeResp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        return self._body


class FakeForge:
    def __init__(self) -> None:
        self.hooks: list[dict[str, Any]] = []
        self.posts: list[Any] = []

    async def get(self, path: str, **kwargs: Any) -> FakeResp:
        return FakeResp(200, self.hooks)

    async def post(self, path: str, *, json: Any = None, **kwargs: Any) -> FakeResp:
        self.posts.append(json)
        self.hooks.append({"config": {"url": json["config"]["url"]}})
        return FakeResp(201, {"id": 1})


def fact(owner: str, repo: str, **facts: Any) -> dict[str, Any]:
    return {"owner": owner, "repo": repo, "archived": False, "default_branch": "main", "facts": facts}


def make_app(monkeypatch: pytest.MonkeyPatch, **config: Any) -> Any:
    settings: dict[str, Any] = {
        "auth_dev_mode": True,
        "webhook_dev_mode": True,
        "github_token": "env-token",
        "github_webhook_secret": "s3cret",
        "webhook_public_url": "https://hooks.example.com",
    }
    settings.update(config)
    app = create_app(Embry0Config(_env_file=None, **settings))
    orgs = FakeOrgsRepo()
    orgs.orgs["acme"] = Organization(owner="acme", forge="github", access={"hooks": True, "read": True})
    orgs.orgs["paused"] = Organization(owner="paused", forge="github", status="paused")
    app.state.organizations_repo = orgs
    facts = FakeFactsRepo()
    facts.rows = [
        fact("acme", "acme/widgets", boot_hint="npm run dev", hook_present=False),
        fact("acme", "acme/gadgets", hook_present=False),
        fact("acme", "acme/one", boot_hint="npm run dev"),
        fact("acme", "acme/two", boot_hint="npm run dev"),
    ]
    app.state.org_repos_repo = facts
    app.state.repos_repo = FakeRegistry()
    app.state.watcher_stacks_repo = FakeStacks()
    app.state.qa_configs_repo = FakeQa()
    jobs = FakeJobs()
    app.state.jobs_repo = jobs
    executor = MagicMock()

    async def start_onboard_job(repo: str, branch: str, **kwargs: Any) -> str:
        job_id = f"job-{len(jobs.rows) + 1}"
        jobs.rows.append({"job_id": job_id, "repo": repo, "pipeline_template": "onboard", "status": "pending"})
        return job_id

    executor.start_onboard_job = AsyncMock(side_effect=start_onboard_job)
    app.state.issue_executor = executor
    app.state.db = None
    app.state.users_repo = None
    app.state.provider_repo = None
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    forge = FakeForge()
    app.state.fake_forge = forge
    app.state.forge_clients_built = []

    async def fake_forge_client(request: Any, owner: str, forge_name: str, credential_id: str | None) -> FakeForge:
        app.state.forge_clients_built.append(owner)
        return forge

    monkeypatch.setattr(org_actions_api, "_forge_client", fake_forge_client)
    # Every path an action reuses writes its own audit row; collect them all.
    audits: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_emit_audit(
        db: Any, action: str, *, actor: str = "system", details: dict[str, Any] | None = None, **kw: Any
    ) -> None:
        audits.append((action, actor, dict(details or {})))

    for module in (org_actions_api, jobs_api, watcher_stacks_api, organizations_api, repo_allowlist):
        monkeypatch.setattr(module, "emit_audit", fake_emit_audit)
    app.state.audits = audits
    # The rediscovery an action starts is recorded here; the one test that
    # runs the real start puts it back.
    rediscoveries: list[str] = []

    async def fake_start_discovery(request: Any, org: Organization) -> dict[str, Any]:
        run = await facts.start_run(org.owner)
        rediscoveries.append(run["id"])
        return run

    monkeypatch.setattr(org_actions_api, "start_discovery", fake_start_discovery)
    app.state.rediscoveries = rediscoveries
    return app


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    return make_app(monkeypatch)


@pytest.fixture
def keyed_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Authentication on, as in production: bearer keys from ``KEYS``."""
    bust_api_key_cache()
    yield make_app(monkeypatch, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")
    bust_api_key_cache()


def _client(app: Any, token: str | None = None) -> AsyncClient:
    headers = {"X-Requested-With": "XMLHttpRequest"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


def _audits(app: Any, action: str) -> list[tuple[str, dict[str, Any]]]:
    return [(actor, details) for name, actor, details in app.state.audits if name == action]


@pytest.mark.asyncio
async def test_register_returns_one_outcome_per_repo_and_is_idempotent(app: Any) -> None:
    body = {"action": "register", "repos": ["acme/widgets", "acme/gadgets", "acme/unseen"]}
    async with _client(app) as client:
        first = await client.post("/api/v1/orgs/acme/actions", json=body)
        second = await client.post("/api/v1/orgs/acme/actions", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["owner"] == "acme" and first.json()["action"] == "register"
    assert first.json()["dry_run"] is False
    assert [(r["repo"], r["outcome"]) for r in first.json()["results"]] == [
        ("acme/widgets", "done"),
        ("acme/gadgets", "done"),
        ("acme/unseen", "skipped"),
    ]
    assert [r["outcome"] for r in second.json()["results"]] == ["already", "already", "skipped"]
    assert set(app.state.repos_repo.rows) == {"acme/widgets", "acme/gadgets"}
    assert set(first.json()["results"][0]) == {"repo", "outcome", "detail"}
    assert _audits(app, "organization.action")[0][1] == {
        "owner": "acme",
        "action": "register",
        "force": False,
        "results": [
            {"repo": "acme/widgets", "outcome": "done"},
            {"repo": "acme/gadgets", "outcome": "done"},
            {"repo": "acme/unseen", "outcome": "skipped"},
        ],
    }


@pytest.mark.asyncio
async def test_dry_run_is_echoed_changes_nothing_and_writes_no_audit(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/orgs/acme/actions", json={"action": "watch", "repos": ["acme/widgets"], "dry_run": True}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["dry_run"] is True
    assert resp.json()["results"][0]["outcome"] == "done"
    assert resp.json()["rediscovery"] is None
    assert app.state.watcher_stacks_repo.rows == {}
    assert app.state.audits == []
    assert app.state.rediscoveries == []


@pytest.mark.asyncio
async def test_hook_uses_the_owner_credential_and_the_webhook_url(app: Any) -> None:
    async with _client(app) as client:
        first = await client.post("/api/v1/orgs/acme/actions", json={"action": "hook", "repos": ["acme/widgets"]})
        second = await client.post("/api/v1/orgs/acme/actions", json={"action": "hook", "repos": ["acme/widgets"]})
    assert first.json()["results"][0]["outcome"] == "done", first.text
    assert second.json()["results"][0]["outcome"] == "already"
    assert len(app.state.fake_forge.posts) == 1
    assert app.state.fake_forge.posts[0]["config"] == {"url": HOOK_URL, "content_type": "json", "secret": "s3cret"}
    assert app.state.forge_clients_built == ["acme", "acme"]


@pytest.mark.asyncio
async def test_a_hook_on_the_dashboard_origins_is_not_this_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only WEBHOOK_PUBLIC_URL is an origin a forge delivers to; the dashboard's vhost 404s webhooks."""
    app = make_app(
        monkeypatch,
        sso_public_origin="https://embry0.example.com",
        dashboard_public_url="https://dash.example.com",
    )
    app.state.fake_forge.hooks = [
        {"config": {"url": "https://embry0.example.com/api/v1/webhook"}},
        {"config": {"url": "https://dash.example.com/api/v1/webhook"}},
    ]
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/actions", json={"action": "hook", "repos": ["acme/widgets"]})
    assert resp.json()["results"][0]["outcome"] == "done", resp.text
    assert [post["config"]["url"] for post in app.state.fake_forge.posts] == [HOOK_URL]


@pytest.mark.asyncio
async def test_hook_is_skipped_when_the_probe_did_not_grant_hooks(app: Any) -> None:
    app.state.organizations_repo.orgs["acme"].access = {"hooks": False, "read": True}
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/actions", json={"action": "hook", "repos": ["acme/widgets"]})
    assert resp.json()["results"][0]["outcome"] == "skipped"
    assert "cannot manage webhooks" in resp.json()["results"][0]["detail"]
    assert app.state.fake_forge.posts == []
    assert app.state.forge_clients_built == []


@pytest.mark.asyncio
async def test_hook_without_a_webhook_url_is_skipped_and_reads_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(monkeypatch, webhook_public_url="", sso_public_origin="https://embry0.example.com")
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/actions", json={"action": "hook", "repos": ["acme/widgets"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["outcome"] == "skipped"
    assert "WEBHOOK_PUBLIC_URL" in resp.json()["results"][0]["detail"]
    assert app.state.forge_clients_built == []


@pytest.mark.asyncio
async def test_watch_adds_a_disabled_stack_and_writes_the_stack_audit_row(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/actions", json={"action": "watch", "repos": ["acme/widgets"]})
    assert resp.json()["results"][0]["outcome"] == "done", resp.text
    stack = app.state.watcher_stacks_repo.rows["acme/widgets"]
    assert stack.enabled is False and stack.tracker == "github"
    assert _audits(app, "watcher_stack_updated") == [
        ("embry0:anonymous", {"repo": "acme/widgets", "tracker": "github", "enabled": False, "ignored_settings": []})
    ]
    assert len(_audits(app, "organization.action")) == 1


@pytest.mark.asyncio
async def test_watch_matches_an_existing_stack_under_another_case(app: Any) -> None:
    app.state.watcher_stacks_repo.rows["acme/Widgets"] = WatcherStack(repo="acme/Widgets", logql='{app="w"}')
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/actions", json={"action": "watch", "repos": ["acme/widgets"]})
    assert resp.json()["results"][0]["outcome"] == "already", resp.text
    assert set(app.state.watcher_stacks_repo.rows) == {"acme/Widgets"}
    assert _audits(app, "watcher_stack_updated") == []


@pytest.mark.asyncio
async def test_onboard_writes_the_onboard_audit_row_with_the_actor(keyed_app: Any) -> None:
    keyed_app.state.repos_repo.rows["acme/widgets"] = {"repo": "acme/widgets"}
    async with _client(keyed_app, ADMIN_TOKEN) as client:
        resp = await client.post("/api/v1/orgs/acme/actions", json={"action": "onboard", "repos": ["acme/widgets"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["outcome"] == "done"
    assert _audits(keyed_app, "onboard_job_created") == [
        ("key:org-admin", {"job_id": "job-1", "repo": "acme/widgets", "branch": "main"})
    ]
    call = keyed_app.state.issue_executor.start_onboard_job.await_args
    assert call.kwargs["dispatched_by"] == "key:org-admin"


@pytest.mark.asyncio
async def test_onboard_honors_the_callers_repository_allowlist(keyed_app: Any) -> None:
    """POST /jobs would refuse this key on acme/two, so the action must not start a job there."""
    for repo in ("acme/one", "acme/two"):
        keyed_app.state.repos_repo.rows[repo] = {"repo": repo}
    body = {"action": "onboard", "repos": ["acme/one", "acme/two"]}
    async with _client(keyed_app, LIMITED_ADMIN_TOKEN) as client:
        dry = await client.post("/api/v1/orgs/acme/actions", json={**body, "dry_run": True})
        assert keyed_app.state.audits == []
        real = await client.post("/api/v1/orgs/acme/actions", json=body)
    for resp in (dry, real):
        assert resp.status_code == 200, resp.text
        assert [(r["repo"], r["outcome"]) for r in resp.json()["results"]] == [
            ("acme/one", "done"),
            ("acme/two", "failed"),
        ]
        assert resp.json()["results"][1]["detail"].startswith("repo_not_allowed")
    started = [c.kwargs["repo"] for c in keyed_app.state.issue_executor.start_onboard_job.await_args_list]
    assert started == ["acme/one"]
    assert [d["repo"] for _actor, d in _audits(keyed_app, "onboard_job_created")] == ["acme/one"]
    assert _audits(keyed_app, "apikey.repo_refused")[0][1]["repo"] == "acme/two"


@pytest.mark.asyncio
async def test_onboard_holds_a_rerun_after_a_completed_job_unless_forced(app: Any) -> None:
    app.state.repos_repo.rows["acme/widgets"] = {"repo": "acme/widgets"}
    app.state.jobs_repo.rows.append(
        {"job_id": "job-7", "repo": "acme/widgets", "pipeline_template": "onboard", "status": "completed"}
    )
    body = {"action": "onboard", "repos": ["acme/widgets"]}
    async with _client(app) as client:
        held = await client.post("/api/v1/orgs/acme/actions", json=body)
        forced = await client.post("/api/v1/orgs/acme/actions", json={**body, "force": True})
    assert held.json()["results"][0]["outcome"] == "skipped", held.text
    assert "job-7" in held.json()["results"][0]["detail"]
    assert forced.json()["results"][0]["outcome"] == "done", forced.text
    assert app.state.issue_executor.start_onboard_job.await_count == 1
    assert [d["force"] for _actor, d in _audits(app, "organization.action")] == [False, True]


@pytest.mark.asyncio
async def test_deactivate_sets_the_registration_inactive_once_and_rediscovers(app: Any) -> None:
    """ADR 0016: the answer to repo_vanished. A dry run writes nothing; the
    real call writes the row inactive, the audit row and a rediscovery, so
    the need settles; the second call is already."""
    app.state.repos_repo.rows["acme/widgets"] = {"repo": "acme/widgets", "active": True}
    body = {"action": "deactivate", "repos": ["acme/widgets", "acme/gadgets"]}
    async with _client(app) as client:
        dry = await client.post("/api/v1/orgs/acme/actions", json={**body, "dry_run": True})
        first = await client.post("/api/v1/orgs/acme/actions", json=body)
        app.state.org_repos_repo.runs[0]["status"] = "done"
        second = await client.post("/api/v1/orgs/acme/actions", json=body)
    assert dry.status_code == 200, dry.text
    assert [r["outcome"] for r in dry.json()["results"]] == ["done", "already"]
    assert dry.json()["rediscovery"] is None
    assert [(r["repo"], r["outcome"]) for r in first.json()["results"]] == [
        ("acme/widgets", "done"),
        ("acme/gadgets", "already"),
    ]
    assert first.json()["rediscovery"] == "odr-1"
    assert [r["outcome"] for r in second.json()["results"]] == ["already", "already"]
    assert second.json()["rediscovery"] is None
    assert app.state.repos_repo.rows == {"acme/widgets": {"repo": "acme/widgets", "active": False}}
    assert [d["action"] for _actor, d in _audits(app, "organization.action")] == ["deactivate", "deactivate"]


@pytest.mark.asyncio
async def test_onboard_without_an_executor_is_reported_not_raised(app: Any) -> None:
    app.state.issue_executor = None
    app.state.repos_repo.rows["acme/widgets"] = {"repo": "acme/widgets"}
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/actions", json={"action": "onboard", "repos": ["acme/widgets"]})
    assert resp.status_code == 200
    assert resp.json()["results"][0]["outcome"] == "failed"
    assert "executor" in resp.json()["results"][0]["detail"]


@pytest.mark.asyncio
async def test_a_change_starts_a_rediscovery_unless_one_is_running(app: Any) -> None:
    async with _client(app) as client:
        changed = await client.post("/api/v1/orgs/acme/actions", json={"action": "watch", "repos": ["acme/widgets"]})
        busy = await client.post("/api/v1/orgs/acme/actions", json={"action": "watch", "repos": ["acme/gadgets"]})
        app.state.org_repos_repo.runs[0]["status"] = "done"
        unchanged = await client.post(
            "/api/v1/orgs/acme/actions", json={"action": "watch", "repos": ["acme/widgets", "acme/unseen"]}
        )
    assert changed.json()["rediscovery"] == "odr-1"
    assert busy.json()["results"][0]["outcome"] == "done" and busy.json()["rediscovery"] is None
    assert [r["outcome"] for r in unchanged.json()["results"]] == ["already", "skipped"]
    assert unchanged.json()["rediscovery"] is None
    assert app.state.rediscoveries == ["odr-1"]


@pytest.mark.asyncio
async def test_the_rediscovery_is_the_discover_endpoints_own_run(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(org_actions_api, "start_discovery", organizations_api.start_discovery)
    walked: list[str] = []

    async def fake_discover(client: Any, owner: str, **kw: Any) -> org_discovery.Discovery:
        walked.append(owner)
        return org_discovery.Discovery([])

    monkeypatch.setattr(org_discovery, "discover", fake_discover)
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/acme/actions", json={"action": "register", "repos": ["acme/widgets"]})
        for _ in range(50):
            if app.state.org_repos_repo.runs[0]["status"] != "running":
                break
            await asyncio.sleep(0.01)
    assert resp.json()["rediscovery"] == "odr-1", resp.text
    assert app.state.org_repos_repo.runs[0]["status"] == "done"
    assert walked == ["acme"]
    assert _audits(app, "organization.discover") == [("embry0:anonymous", {"owner": "acme", "run_id": "odr-1"})]


@pytest.mark.asyncio
async def test_an_operator_tier_key_is_refused(keyed_app: Any) -> None:
    async with _client(keyed_app, OPERATOR_TOKEN) as client:
        resp = await client.post("/api/v1/orgs/acme/actions", json={"action": "register", "repos": ["acme/widgets"]})
        admin = await client.post(
            "/api/v1/orgs/acme/actions",
            json={"action": "register", "repos": ["acme/widgets"]},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
    assert resp.status_code == 403, resp.text
    assert admin.status_code == 200, admin.text
    assert [r["repo"] for r in admin.json()["results"]] == ["acme/widgets"]
    assert set(keyed_app.state.repos_repo.rows) == {"acme/widgets"}
    assert [actor for actor, _details in _audits(keyed_app, "organization.action")] == ["key:org-admin"]


@pytest.mark.asyncio
async def test_unknown_owner_paused_owner_and_bad_bodies(app: Any) -> None:
    async with _client(app) as client:
        missing = await client.post("/api/v1/orgs/nobody/actions", json={"action": "register", "repos": ["x/y"]})
        paused = await client.post("/api/v1/orgs/paused/actions", json={"action": "register", "repos": ["paused/a"]})
        bad_action = await client.post(
            "/api/v1/orgs/acme/actions", json={"action": "delete", "repos": ["acme/widgets"]}
        )
        no_repos = await client.post("/api/v1/orgs/acme/actions", json={"action": "register", "repos": []})
    assert missing.status_code == 404
    assert paused.status_code == 409
    assert bad_action.status_code == 422
    assert no_repos.status_code == 422


@pytest.mark.asyncio
async def test_a_post_without_the_csrf_header_is_refused(app: Any) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/orgs/acme/actions", json={"action": "register", "repos": ["acme/widgets"]})
    assert resp.status_code == 403
    assert app.state.repos_repo.rows == {}
