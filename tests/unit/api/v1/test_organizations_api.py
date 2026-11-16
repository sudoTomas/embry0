"""Organizations API (ADR 0010): registering the owners embry0 maintains."""

from __future__ import annotations

import socket
from collections.abc import Callable, Sequence
from typing import Any

import httpcore
import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from embry0.api.app import create_app
from embry0.api.v1 import organizations as organizations_api
from embry0.config import Embry0Config
from embry0.execution import forge_client
from embry0.services.org_discovery import Discovery, RepoFacts
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

    async def refresh_state(self, repo: str, state: dict[str, Any]) -> None:
        self.facts[repo]["facts"] = {**self.facts[repo]["facts"], **state}

    async def list_for_owner(
        self, owner: str, *, include_archived: bool = True, repos: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        return [
            f
            for f in self.facts.values()
            if f["owner"] == owner.lower()
            and (include_archived or not f["archived"])
            and (repos is None or f["repo"].lower() in repos)
        ]

    async def start_run(self, owner: str) -> dict[str, Any] | None:
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
async def test_probe_answers_what_the_credential_can_do_and_stores_nothing(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/probe", json={"owner": "Acme", "credential_id": "cred-ok"})
        listing = await client.get("/api/v1/orgs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["read"] is True and body["hooks"] is True and body["push"] is None
    assert body["identity"] == "acme-bot" and body["owner_kind"] == "organization"
    assert body["repo_count"] == 12 and body["sample_repo"] == "acme/backend"
    assert body["detail"] == "can list repositories"
    assert body["source"] == "registry:acme-bot"
    assert app.state.probes == [("acme", "github", "registry-token")]
    assert app.state.organizations_repo.orgs == {}
    assert app.state.org_repos_repo.runs == []
    assert listing.json()["organizations"] == []


@pytest.mark.asyncio
async def test_probe_answers_an_owner_the_credential_cannot_list_instead_of_refusing(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/probe", json={"owner": "ghost"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["read"] is False
    assert resp.json()["detail"] == "acme is not visible"
    assert resp.json()["source"] == "env:GITHUB_TOKEN__GHOST"
    assert app.state.organizations_repo.orgs == {}


@pytest.mark.asyncio
async def test_probe_with_an_unknown_credential_is_404_with_a_detail(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/v1/orgs/probe", json={"owner": "acme", "credential_id": "cred-gone"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "credential not found"
    assert app.state.probes == []
    assert app.state.organizations_repo.orgs == {}


@pytest.mark.asyncio
async def test_probe_refuses_a_slash_and_an_unknown_forge(app: Any) -> None:
    async with _client(app) as client:
        slash = await client.post("/api/v1/orgs/probe", json={"owner": "acme/backend"})
        forge = await client.post("/api/v1/orgs/probe", json={"owner": "acme", "forge": "bitbucket"})
    assert slash.status_code == 422 and "no slash" in slash.json()["detail"]
    assert forge.status_code == 422
    assert app.state.probes == []


def test_probe_is_admin_tier_and_declared_before_every_owner_route() -> None:
    from fastapi.routing import APIRoute

    from embry0.api.deps import require_admin

    routes = [r for r in organizations_api.router.routes if isinstance(r, APIRoute)]
    probe = next(r for r in routes if r.path == "/orgs/probe")
    assert probe.methods == {"POST"}
    assert [d.dependency for d in probe.dependencies] == [require_admin.dependency]
    first_owner_route = next(i for i, r in enumerate(routes) if r.path.startswith("/orgs/{owner}"))
    assert routes.index(probe) < first_owner_route


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

    async def fake_discover(client: Any, owner: str, **kw: Any) -> Discovery:
        await kw["progress"](1, 1)
        state = await kw["embry0_state"]("acme/web")
        return Discovery([RepoFacts(owner=owner, repo="acme/web", facts={"frontend": True, **state})])

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("", ()),
        ("http://127.0.0.1:8200", ()),
        ("http://localhost:8200/", ()),
        ("http://[::1]:8200", ()),
        ("http://0.0.0.0", ()),
        ("https://hooks.example.com/", ("https://hooks.example.com",)),
    ],
)
async def test_discovery_reads_hooks_as_unknown_under_an_unset_or_loopback_origin(
    app: Any, monkeypatch: pytest.MonkeyPatch, url: str, expected: tuple[str, ...]
) -> None:
    """No forge delivers to a loopback address, so a hook there cannot be this
    deployment's: discovery gets no origin, reads hooks as unknown and the
    needs list files hook_origin_unknown, as for an unset WEBHOOK_PUBLIC_URL."""
    import asyncio

    from embry0.services import org_discovery

    seen: list[tuple[str, ...]] = []

    async def fake_discover(client: Any, owner: str, **kw: Any) -> Discovery:
        seen.append(kw["hook_origins"])
        return Discovery([])

    monkeypatch.setattr(org_discovery, "discover", fake_discover)
    app.state.repos_repo = None
    app.state.config = Embry0Config(
        _env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="env-token", webhook_public_url=url
    )
    async with _client(app) as client:
        await client.post("/api/v1/orgs", json={"owner": "acme", "probe": False})
        started = await client.post("/api/v1/orgs/acme/discover")
        assert started.status_code == 202, started.text
        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.01)
    assert seen == [expected], f"WEBHOOK_PUBLIC_URL={url!r} gave discovery the origins {seen}"
    hooks = [{"config": {"url": "http://127.0.0.1:8200/api/v1/webhook"}}]
    if not expected:
        assert org_discovery.hook_state(hooks, seen[0]) is None, "hooks must read as unknown, not absent"


# ---- rediscovery (ADR 0016) ----


async def _wait_for_the_newest_run(client: AsyncClient) -> dict[str, Any]:
    import asyncio

    for _ in range(100):
        runs = (await client.get("/api/v1/orgs/acme/runs")).json()["runs"]
        if runs and runs[-1]["status"] != "running":
            return dict(runs[-1])
        await asyncio.sleep(0.01)
    raise AssertionError(f"the run never finished: {runs}")


@pytest.mark.asyncio
async def test_a_repository_the_next_run_does_not_list_has_its_registration_read_again(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second run does not list acme/api. Its row keeps the run that last
    listed it, the summary counts it, and whether embry0 still holds it as an
    active registration is read from the registry now, not kept from then."""
    from embry0.services import org_discovery

    listings = [["acme/web", "acme/api"], ["acme/web"]]

    async def fake_discover(client: Any, owner: str, **kw: Any) -> Discovery:
        return Discovery(
            [RepoFacts(owner=owner, repo=name, facts={**(await kw["embry0_state"](name))}) for name in listings.pop(0)]
        )

    registry = {"acme/api": {"repo": "acme/api", "active": True}}

    class Registry:
        async def get(self, repo: str) -> dict[str, Any] | None:
            return registry.get(repo)

    monkeypatch.setattr(org_discovery, "discover", fake_discover)
    app.state.repos_repo = Registry()
    async with _client(app) as client:
        await client.post("/api/v1/orgs", json={"owner": "acme", "probe": False})
        assert (await client.post("/api/v1/orgs/acme/discover")).status_code == 202
        first = await _wait_for_the_newest_run(client)
        # A person deactivated it between the runs.
        registry["acme/api"] = {"repo": "acme/api", "active": False}
        assert (await client.post("/api/v1/orgs/acme/discover")).status_code == 202
        second = await _wait_for_the_newest_run(client)
        rows = {r["repo"]: r for r in (await client.get("/api/v1/orgs/acme/repos")).json()["repos"]}
    assert first["status"] == "done" and first["summary"]["vanished"] == 0
    assert second["status"] == "done" and second["summary"]["vanished"] == 1
    assert second["summary"]["vanished_confirmed"] == 0, "an inactive registration is not read on the forge"
    assert second["summary"]["listing_complete"] is True
    assert rows["acme/web"]["run_id"] == second["id"]
    assert rows["acme/api"]["run_id"] == first["id"], "the row still says which run last listed it"
    assert rows["acme/api"]["facts"]["registered"] is True and rows["acme/api"]["facts"]["active"] is False
    assert rows["acme/api"]["facts"]["vanished_run"] is None


@pytest.mark.asyncio
async def test_a_start_that_loses_the_race_for_the_owner_is_refused(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The repository's own check said a run is already open (another start
    got there between the endpoint's check and the insert): 409, no task."""

    async def lost(owner: str) -> None:
        return None

    monkeypatch.setattr(app.state.org_repos_repo, "start_run", lost)
    async with _client(app) as client:
        await client.post("/api/v1/orgs", json={"owner": "acme", "probe": False})
        resp = await client.post("/api/v1/orgs/acme/discover")
    assert resp.status_code == 409 and "already running" in resp.json()["detail"]
    assert not getattr(app.state, "org_discovery_tasks", set())


@pytest.mark.asyncio
async def test_the_schedule_starts_the_discover_endpoints_own_run(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop starts runs through start_discovery with a request bound to
    the app, audited as the schedule, and never joins a run in progress."""
    import asyncio

    from embry0.services import org_discovery
    from embry0.services.org_rediscovery import ACTOR, OrgRediscoveryLoop

    release = asyncio.Event()
    walked: list[str] = []

    async def fake_discover(client: Any, owner: str, **kw: Any) -> Discovery:
        walked.append(owner)
        await release.wait()
        return Discovery([])

    audits: list[tuple[str, str, dict[str, Any]]] = []

    async def record(_db: Any, action: str, **kw: Any) -> None:
        audits.append((action, kw.get("actor", ""), kw.get("details") or {}))

    monkeypatch.setattr(org_discovery, "discover", fake_discover)
    monkeypatch.setattr(organizations_api, "emit_audit", record)
    app.state.repos_repo = None
    async with _client(app) as client:
        await client.post("/api/v1/orgs", json={"owner": "acme", "probe": False})
        await client.post("/api/v1/orgs", json={"owner": "other", "probe": False})
    app.state.organizations_repo.orgs["acme"].settings = {"rediscover_hours": 6}

    def start_run(org: Organization) -> Any:
        return organizations_api.start_discovery(organizations_api.app_request(app), org, actor=ACTOR)

    loop = OrgRediscoveryLoop(
        organizations=app.state.organizations_repo, runs=app.state.org_repos_repo, start_run=start_run
    )
    assert await loop.tick() == ["acme"], "only the owner on the schedule"
    for _ in range(100):
        if walked:
            break
        await asyncio.sleep(0.01)
    assert walked == ["acme"]
    assert await loop.tick() == [], "the run is still going"
    release.set()
    async with _client(app) as client:
        newest = await _wait_for_the_newest_run(client)
    assert newest["status"] == "done"
    assert [(a, actor, d) for a, actor, d in audits if a == "organization.discover"] == [
        ("organization.discover", ACTOR, {"owner": "acme", "run_id": newest["id"]})
    ]


# ---- a forge that never answers (#379) ----------------------------------------

#: The real probe, taken before the fixture stands a fake in for it.
_REAL_PROBE = organizations_api.probe_access


def _raised_as_httpx_does(
    error: httpx.RequestError, transport: Exception, os_error: BaseException
) -> httpx.RequestError:
    """``error`` as httpx raises it: caused by the transport's error (httpcore's), which was raised while handling
    what the OS said."""
    error.__cause__ = transport
    transport.__context__ = os_error
    return error


#: Each way a forge goes unanswered: the error the client raises, built as httpx raises it.
_UNREACHABLE: dict[str, Callable[[], httpx.RequestError]] = {
    "refused": lambda: _raised_as_httpx_does(
        httpx.ConnectError("All connection attempts failed"),
        httpcore.ConnectError("All connection attempts failed"),
        _caused(OSError("All connection attempts failed"), ConnectionRefusedError(111, "Connection refused")),
    ),
    "unresolved": lambda: _raised_as_httpx_does(
        httpx.ConnectError("[Errno -2] Name or service not known"),
        httpcore.ConnectError("[Errno -2] Name or service not known"),
        socket.gaierror(-2, "Name or service not known"),
    ),
    "timeout": lambda: _raised_as_httpx_does(
        httpx.ReadTimeout("timed out"), httpcore.ReadTimeout("timed out"), TimeoutError("timed out")
    ),
}
#: The class each one's chain ends in: what failed under the client.
_ROOT = {"refused": "ConnectionRefusedError", "unresolved": "gaierror", "timeout": "TimeoutError"}


def _caused(error: BaseException, cause: BaseException) -> BaseException:
    error.__cause__ = cause
    return error


def _forge_unreachable(monkeypatch: pytest.MonkeyPatch, how: str) -> list[str]:
    """The real probe, with every request the forge clients send failing at the transport as ``how`` says, as with
    the forge down. Answers the URLs it was asked for, so a test can show the probe asked at all."""
    asked: list[str] = []
    real_client = httpx.AsyncClient

    def unreachable(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        raise _UNREACHABLE[how]()

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(unreachable), **kwargs)

    monkeypatch.setattr(forge_client.httpx, "AsyncClient", client)
    monkeypatch.setattr(organizations_api, "probe_access", _REAL_PROBE)
    return asked


async def _answer(app: Any, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
    """What a caller is answered: an exception the app does not handle is its 500, not raised into the test."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        return await client.post(path, json=body)


@pytest.mark.parametrize("error", list(_UNREACHABLE), ids=list(_UNREACHABLE))
@pytest.mark.asyncio
async def test_a_probe_github_never_answers_is_a_502_naming_it(
    app: Any, monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    asked = _forge_unreachable(monkeypatch, error)
    with capture_logs() as logs:
        resp = await _answer(app, "/api/v1/orgs/probe", {"owner": "acme"})
    assert asked, "the probe never asked GitHub"
    assert resp.status_code == 502, f"a probe GitHub never answered ({error}): {resp.status_code} {resp.text}"
    assert resp.json()["detail"] == "GitHub could not be reached"
    (logged,) = [e for e in logs if e["event"] == "organization_probe_unreachable"]
    assert (logged["owner"], logged["forge"], logged["error"], logged["cause"]) == (
        "acme",
        "github",
        type(_UNREACHABLE[error]()).__name__,
        _ROOT[error],
    ), f"logged with the client's error and the class of what failed under it: {logged}"


@pytest.mark.asyncio
async def test_a_probe_gitlab_never_answers_is_a_502_naming_it(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    app.state.config.gitlab_token = "glpat-placeholder"
    asked = _forge_unreachable(monkeypatch, "refused")
    resp = await _answer(app, "/api/v1/orgs/probe", {"owner": "acme", "forge": "gitlab"})
    assert asked, "the probe never asked GitLab"
    assert resp.status_code == 502, f"a probe GitLab never answered: {resp.status_code} {resp.text}"
    assert resp.json()["detail"] == "GitLab could not be reached"


@pytest.mark.asyncio
async def test_a_reprobe_github_never_answers_is_a_502_and_keeps_what_was_stored(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _client(app) as client:
        # Registered with the stand-in probe: what a probe that got answers stored.
        assert (await client.post("/api/v1/orgs", json={"owner": "acme"})).status_code == 201
    org = app.state.organizations_repo.orgs["acme"]
    stored, count = dict(org.access), org.repo_count
    assert (stored["read"], stored["hooks"], stored["owner_kind"], count) == (True, True, "organization", 12), stored
    _forge_unreachable(monkeypatch, "timeout")
    resp = await _answer(app, "/api/v1/orgs/acme/probe")
    assert resp.status_code == 502, f"a re-probe GitHub never answered: {resp.status_code} {resp.text}"
    assert resp.json()["detail"] == "GitHub could not be reached"
    assert (org.access, org.repo_count) == (stored, count), "a re-probe that got no answer rewrote the stored access"


@pytest.mark.asyncio
async def test_registering_with_a_probe_github_never_answers_is_a_502_and_stores_nothing(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forge_unreachable(monkeypatch, "unresolved")
    resp = await _answer(app, "/api/v1/orgs", {"owner": "acme"})
    assert resp.status_code == 502, f"a registration whose probe GitHub never answered: {resp.status_code} {resp.text}"
    assert resp.json()["detail"] == "GitHub could not be reached"
    assert app.state.organizations_repo.orgs == {}, "an owner whose probe got no answer was registered"
