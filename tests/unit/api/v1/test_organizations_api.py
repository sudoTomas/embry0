"""Organizations API (ADR 0010): registering the owners embry0 maintains."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import organizations as organizations_api
from embry0.config import Embry0Config
from embry0.services.org_discovery import RepoFacts
from embry0.services.organizations import FORGES, STATUSES, AccessProbe, Organization


class FakeOrgsRepo:
    def __init__(self) -> None:
        self.orgs: dict[str, Organization] = {}

    async def list_all(self, *, include_removed: bool = False) -> list[Organization]:
        return [o for o in self.orgs.values() if include_removed or o.status != "removed"]

    async def get(self, owner: str) -> Organization | None:
        return self.orgs.get(owner.lower())

    async def register(self, owner: str, forge: str, **kw: Any) -> Organization:
        owner = owner.lower()
        if forge not in FORGES:
            raise ValueError(f"unknown forge {forge!r}")
        org = Organization(
            owner=owner,
            forge=forge,
            credential_id=kw.get("credential_id"),
            display_name=kw.get("display_name"),
            settings=dict(kw.get("settings") or {}),
            created_by=kw.get("created_by"),
        )
        self.orgs[owner] = org
        return org

    async def update_access(
        self, owner: str, access: dict[str, Any], *, repo_count: int | None = None
    ) -> Organization | None:
        org = self.orgs.get(owner.lower())
        if org is None:
            return None
        org.access = dict(access)
        org.repo_count = repo_count if repo_count is not None else org.repo_count
        return org

    async def set_status(self, owner: str, status: str) -> Organization | None:
        if status not in STATUSES:
            raise ValueError(status)
        org = self.orgs.get(owner.lower())
        if org is None:
            return None
        org.status = status
        return org

    async def mark_discovered(self, owner: str, *, repo_count: int, when: Any = None) -> Organization | None:
        org = self.orgs.get(owner.lower())
        if org is not None:
            org.repo_count = repo_count
        return org


class FakeFactsRepo:
    def __init__(self) -> None:
        self.facts: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []

    async def upsert_fact(self, facts: RepoFacts, *, run_id: str) -> None:
        self.facts[facts.repo] = {**facts.to_dict(), "run_id": run_id}

    async def list_for_owner(self, owner: str, *, include_archived: bool = True) -> list[dict[str, Any]]:
        return [
            f for f in self.facts.values() if f["owner"] == owner.lower() and (include_archived or not f["archived"])
        ]

    async def start_run(self, owner: str) -> dict[str, Any]:
        run = {
            "id": f"odr-{len(self.runs) + 1}",
            "owner": owner,
            "status": "running",
            "repos_total": None,
            "repos_seen": 0,
            "summary": {},
            "error": None,
        }
        self.runs.append(run)
        return run

    async def progress(self, run_id: str, *, seen: int, total: int) -> None:
        for r in self.runs:
            if r["id"] == run_id:
                r["repos_seen"], r["repos_total"] = seen, total

    async def finish_run(
        self, run_id: str, *, summary: dict[str, Any] | None = None, error: str | None = None
    ) -> dict[str, Any] | None:
        for r in self.runs:
            if r["id"] == run_id:
                r.update(status="error" if error else "done", summary=dict(summary or {}), error=error)
                return r
        return None

    async def running_run(self, owner: str) -> dict[str, Any] | None:
        return next((r for r in self.runs if r["owner"] == owner.lower() and r["status"] == "running"), None)

    async def list_runs(self, owner: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return [r for r in self.runs if r["owner"] == owner.lower()][-limit:]


class FakeCredentials:
    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    async def get(self, credential_id: str) -> dict[str, Any] | None:
        return self.rows.get(credential_id)


class FakeSecrets:
    async def decrypt(self, ciphertext: str) -> str:
        return ciphertext.removeprefix("enc:")


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="env-token")
    app = create_app(config)
    app.state.organizations_repo = FakeOrgsRepo()
    app.state.org_repos_repo = FakeFactsRepo()
    app.state.credentials_repo = FakeCredentials(
        {
            "cred-ok": {
                "id": "cred-ok",
                "kind": "forge_token",
                "provider": "github",
                "status": "ok",
                "label": "acme-bot",
                "secret_ciphertext": "enc:registry-token",
            },
            "cred-llm": {
                "id": "cred-llm",
                "kind": "llm_api_key",
                "provider": "anthropic",
                "status": "ok",
                "label": "claude",
                "secret_ciphertext": "enc:x",
            },
        }
    )
    app.state.secrets_provider = FakeSecrets()
    app.state.db = None

    probes: list[tuple[str, str, str]] = []

    async def fake_probe(request: Any, owner: str, forge: str, token: str) -> AccessProbe:
        probes.append((owner, forge, token))
        readable = owner != "ghost"
        return AccessProbe(
            identity="acme-bot",
            owner_kind="organization" if readable else None,
            read=readable,
            hooks=readable,
            push=None,
            repo_count=12 if readable else None,
            sample_repo="acme/backend" if readable else None,
            detail="can list repositories" if readable else "acme is not visible",
        )

    monkeypatch.setattr(organizations_api, "probe_access", fake_probe)
    app.state.probes = probes
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    )


@pytest.mark.asyncio
async def test_register_probes_the_registry_credential_and_stores_what_it_can_do(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs", json={"owner": "Acme", "forge": "github", "credential_id": "cred-ok"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["owner"] == "acme"
    assert body["access"]["read"] is True
    assert body["access"]["hooks"] is True
    assert body["access"]["source"] == "registry:acme-bot"
    assert body["repo_count"] == 12
    assert app.state.probes == [("acme", "github", "registry-token")]


@pytest.mark.asyncio
async def test_register_falls_back_to_the_owner_env_token(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs", json={"owner": "acme"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["access"]["source"] == "env:GITHUB_TOKEN__ACME"
    assert app.state.probes[-1][2] == "env-token"


@pytest.mark.asyncio
async def test_register_refuses_an_owner_the_credential_cannot_list(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs", json={"owner": "ghost", "credential_id": "cred-ok"})
    assert resp.status_code == 422
    assert "cannot list ghost" in resp.json()["detail"]
    assert await app.state.organizations_repo.get("ghost") is None


@pytest.mark.asyncio
async def test_register_refuses_a_credential_that_is_not_a_forge_token(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs", json={"owner": "acme", "credential_id": "cred-llm"})
    assert resp.status_code == 422
    assert "not a github forge token" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_without_probe_stores_the_owner_as_given(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs", json={"owner": "acme", "probe": False})
        listing = await client.get("/api/v1/orgs")
        one = await client.get("/api/v1/orgs/acme")
    assert resp.status_code == 201
    assert resp.json()["access"] == {}
    assert app.state.probes == []
    assert [o["owner"] for o in listing.json()["organizations"]] == ["acme"]
    assert one.json()["forge"] == "github"


@pytest.mark.asyncio
async def test_slash_is_a_repository_not_an_owner(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs", json={"owner": "acme/backend", "probe": False})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_status_and_removed_filtering(app: Any) -> None:
    async with _client(app) as client:
        await client.post("/api/v1/orgs", json={"owner": "acme", "probe": False})
        resp = await client.put("/api/v1/orgs/acme/status", json={"status": "removed"})
        hidden = await client.get("/api/v1/orgs")
        shown = await client.get("/api/v1/orgs", params={"include_removed": "true"})
        missing = await client.put("/api/v1/orgs/nobody/status", json={"status": "paused"})
    assert resp.status_code == 200 and resp.json()["status"] == "removed"
    assert hidden.json()["organizations"] == []
    assert len(shown.json()["organizations"]) == 1
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_reprobe_rewrites_access(app: Any) -> None:
    async with _client(app) as client:
        await client.post("/api/v1/orgs", json={"owner": "acme", "probe": False})
        resp = await client.post("/api/v1/orgs/acme/probe")
    assert resp.status_code == 200
    assert resp.json()["access"]["read"] is True
    assert resp.json()["access"]["source"] == "env:GITHUB_TOKEN__ACME"


@pytest.mark.asyncio
async def test_reprobe_writes_an_audit_row(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The re-probe rewrites access, which authorises hook installs, so it is audited like every other mutation."""
    rows: list[tuple[str, dict[str, Any]]] = []

    async def record(_db: Any, action: str, **kw: Any) -> None:
        rows.append((action, kw.get("details") or {}))

    monkeypatch.setattr(organizations_api, "emit_audit", record)
    async with _client(app) as client:
        await client.post("/api/v1/orgs", json={"owner": "acme", "probe": False})
        resp = await client.post("/api/v1/orgs/acme/probe")
    assert resp.status_code == 200
    probes = [details for action, details in rows if action == "organization.probe"]
    assert probes, f"POST /orgs/{{owner}}/probe wrote no organization.probe audit row (rows: {[a for a, _ in rows]})"
    assert probes[0]["owner"] == "acme"
    assert probes[0]["source"] == "env:GITHUB_TOKEN__ACME"
    assert probes[0]["read"] is True and "hooks" in probes[0]


@pytest.mark.asyncio
async def test_discover_runs_in_the_background_and_stores_facts(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from embry0.api.v1 import organizations as api_module
    from embry0.services import org_discovery

    async def fake_discover(client: Any, owner: str, **kw: Any) -> list[RepoFacts]:
        await kw["progress"](1, 1)
        state = await kw["embry0_state"]("acme/web")
        return [RepoFacts(owner=owner, repo="acme/web", facts={"frontend": True, **state})]

    monkeypatch.setattr(org_discovery, "discover", fake_discover)
    app.state.repos_repo = None
    async with _client(app) as client:
        await client.post("/api/v1/orgs", json={"owner": "acme", "probe": False})
        started = await client.post("/api/v1/orgs/acme/discover")
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        for _ in range(50):
            runs = (await client.get("/api/v1/orgs/acme/runs")).json()["runs"]
            if runs and runs[-1]["status"] != "running":
                break
            await asyncio.sleep(0.01)
        repos = await client.get("/api/v1/orgs/acme/repos")
        org = await client.get("/api/v1/orgs/acme")
    assert runs[-1]["id"] == run_id and runs[-1]["status"] == "done", runs
    assert runs[-1]["summary"]["repos"] == 1 and runs[-1]["repos_seen"] == 1
    assert repos.json()["count"] == 1 and repos.json()["repos"][0]["facts"]["frontend"] is True
    assert repos.json()["repos"][0]["facts"]["registered"] is False
    assert org.json()["repo_count"] == 1
    assert api_module is not None


@pytest.mark.asyncio
async def test_discover_refuses_a_second_run_and_a_paused_owner(app: Any) -> None:
    async with _client(app) as client:
        await client.post("/api/v1/orgs", json={"owner": "acme", "probe": False})
        await app.state.org_repos_repo.start_run("acme")
        busy = await client.post("/api/v1/orgs/acme/discover")
        await client.put("/api/v1/orgs/acme/status", json={"status": "paused"})
        paused = await client.post("/api/v1/orgs/acme/discover")
        missing = await client.post("/api/v1/orgs/nobody/discover")
    assert busy.status_code == 409 and "already running" in busy.json()["detail"]
    assert paused.status_code == 409
    assert missing.status_code == 404
