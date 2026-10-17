"""Organization needs API (ADR 0011): the list, dismiss and reopen."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.org_discovery import RepoFacts
from tests.unit.api.v1.test_organizations_api import FakeFactsRepo, FakeOrgsRepo
from tests.unit.services.test_org_needs import FakeCases, FakeNeedsRepo


class NewestFirstFactsRepo(FakeFactsRepo):
    """Runs newest first, the order the real repository answers in."""

    async def list_runs(self, owner: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return list(reversed(await super().list_runs(owner, limit=limit)))


@pytest.fixture
async def app() -> Any:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="env-token")
    app = create_app(config)
    app.state.organizations_repo = FakeOrgsRepo()
    app.state.org_repos_repo = NewestFirstFactsRepo()
    app.state.org_needs_repo = FakeNeedsRepo()
    app.state.healing_cases = FakeCases()
    app.state.db = None
    await app.state.organizations_repo.register("acme", "github")
    run = await app.state.org_repos_repo.start_run("acme")
    await app.state.org_repos_repo.upsert_fact(
        RepoFacts(
            owner="acme",
            repo="acme/api",
            facts={"root_visible": True, "hooks_visible": True, "hook_present": False, "registered": False},
        ),
        run_id=run["id"],
    )
    await app.state.org_repos_repo.upsert_fact(
        RepoFacts(
            owner="acme",
            repo="acme/web",
            facts={
                "root_visible": True,
                "hooks_visible": True,
                "hook_present": True,
                "registered": True,
                "qa_config": True,
                "watcher_stack": True,
                "deploy_marker": True,
            },
        ),
        run_id=run["id"],
    )
    await app.state.org_repos_repo.finish_run(run["id"], summary={"repos": 2})
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    )


@pytest.mark.asyncio
async def test_needs_follow_the_contract_shape(app: Any) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/orgs/Acme/needs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["owner"] == "acme" and body["run_id"] == "odr-1"
    assert set(body) == {"owner", "run_id", "needs", "counts"}
    assert body["counts"] == {"open": 2, "done": 0, "dismissed": 0}
    kinds = {n["kind"]: n for n in body["needs"]}
    assert set(kinds) == {"not_registered", "no_webhook"}
    need = kinds["not_registered"]
    assert set(need) == {"id", "kind", "title", "detail", "repos", "action", "status", "case_id"}
    assert need["repos"] == ["acme/api"] and need["action"] == {"type": "register", "params": {}}
    assert need["status"] == "open" and need["case_id"] in app.state.healing_cases.cases
    assert app.state.healing_cases.cases[need["case_id"]]["origin"] == "org"


@pytest.mark.asyncio
async def test_a_second_read_of_an_unchanged_run_writes_nothing(app: Any) -> None:
    """The first read catches up a run that finished before its needs were
    synced; after that a read only reads."""
    needs, cases = app.state.org_needs_repo, app.state.healing_cases
    async with _client(app) as client:
        first = await client.get("/api/v1/orgs/acme/needs")
        writes, filed = needs.writes, len(cases.cases)
        assert writes > 0 and filed == 2
        second = await client.get("/api/v1/orgs/acme/needs")
    assert second.json() == first.json()
    assert (needs.writes, len(cases.cases), cases.closed) == (writes, filed, [])


@pytest.mark.asyncio
async def test_dismiss_and_reopen_answer_with_the_need(app: Any) -> None:
    async with _client(app) as client:
        listing = (await client.get("/api/v1/orgs/acme/needs")).json()
        need = next(n for n in listing["needs"] if n["kind"] == "no_webhook")
        dismissed = await client.post(f"/api/v1/orgs/acme/needs/{need['id']}/dismiss")
        after = (await client.get("/api/v1/orgs/acme/needs")).json()
        reopened = await client.post(f"/api/v1/orgs/acme/needs/{need['id']}/reopen")
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
    assert dismissed.json()["id"] == need["id"]
    assert app.state.healing_cases.cases[need["case_id"]]["stage"] == "closed"
    assert after["counts"] == {"open": 1, "done": 0, "dismissed": 1}
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"
    assert reopened.json()["case_id"] != need["case_id"]


@pytest.mark.asyncio
async def test_unknown_owner_and_unknown_need_are_404(app: Any) -> None:
    async with _client(app) as client:
        owner = await client.get("/api/v1/orgs/nobody/needs")
        need = await client.post("/api/v1/orgs/acme/needs/need-missing/dismiss")
        reopen = await client.post("/api/v1/orgs/acme/needs/need-missing/reopen")
    assert owner.status_code == 404
    assert need.status_code == 404 and reopen.status_code == 404


@pytest.mark.asyncio
async def test_needs_storage_missing_is_503(app: Any) -> None:
    app.state.org_needs_repo = None
    async with _client(app) as client:
        resp = await client.get("/api/v1/orgs/acme/needs")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_discovery_syncs_the_needs_when_it_finishes(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from embry0.services import org_discovery

    async def fake_discover(client: Any, owner: str, **kw: Any) -> list[RepoFacts]:
        return [
            RepoFacts(
                owner=owner,
                repo="acme/new",
                facts={"root_visible": True, "hooks_visible": True, "hook_present": True, "registered": False},
            )
        ]

    monkeypatch.setattr(org_discovery, "discover", fake_discover)
    app.state.repos_repo = None
    await app.state.organizations_repo.update_access("acme", {"owner_kind": "organization", "repo_count": 3})
    async with _client(app) as client:
        started = await client.post("/api/v1/orgs/acme/discover")
        assert started.status_code == 202, started.text
        for _ in range(50):
            runs = (await client.get("/api/v1/orgs/acme/runs")).json()["runs"]
            if runs[0]["status"] != "running":
                break
            await asyncio.sleep(0.01)
        needs = (await client.get("/api/v1/orgs/acme/needs")).json()
    assert runs[0]["status"] == "done"
    assert runs[0]["summary"]["count_gap"]["owner_says"] == 3 and runs[0]["summary"]["count_gap"]["gap"] == 2
    assert needs["run_id"] == runs[0]["id"]
    kinds = {n["kind"]: n for n in needs["needs"] if n["status"] == "open"}
    assert kinds["not_registered"]["repos"] == ["acme/new"]
    assert kinds["count_gap"]["action"]["type"] == "credential"
    assert "no_webhook" not in kinds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings", "origins"),
    [
        # The dashboard and SSO origins are not webhook origins: the public
        # dashboard answers 404 on webhook paths.
        (
            {"dashboard_public_url": "https://embry0.example.com", "sso_public_origin": "https://embry0.example.com"},
            (),
        ),
        (
            {"dashboard_public_url": "https://embry0.example.com", "webhook_public_url": " https://hooks.example.net/"},
            ("https://hooks.example.net",),
        ),
        ({}, ()),
    ],
)
async def test_discovery_compares_hooks_against_the_webhook_url_only(
    app: Any, monkeypatch: pytest.MonkeyPatch, settings: dict[str, str], origins: tuple[str, ...]
) -> None:
    import asyncio

    from embry0.services import org_discovery

    seen: dict[str, Any] = {}

    async def fake_discover(client: Any, owner: str, **kw: Any) -> list[RepoFacts]:
        seen.update(kw)
        return []

    monkeypatch.setattr(org_discovery, "discover", fake_discover)
    app.state.config = Embry0Config(
        _env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="env-token", **settings
    )
    async with _client(app) as client:
        assert (await client.post("/api/v1/orgs/acme/discover")).status_code == 202
        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.01)
    assert seen["hook_origins"] == origins


@pytest.mark.asyncio
async def test_state_lookup_carries_env_key_names_never_values(app: Any) -> None:
    from starlette.requests import Request

    from embry0.api.v1.organizations import embry0_state_lookup

    class FakeEnv:
        global_reads = 0

        async def get_global(self) -> list[dict[str, Any]]:
            self.global_reads += 1
            return [{"key": "SHARED", "value": "global-secret"}]

        async def get_repo(self, repo: str) -> list[dict[str, Any]]:
            return [{"repo": repo, "key": "DB_URL", "value": "repo-secret"}] if repo == "acme/api" else []

    env = FakeEnv()
    app.state.env_repo = env
    app.state.repos_repo = None
    lookup = embry0_state_lookup(Request({"type": "http", "app": app}))
    api = await lookup("acme/api")
    web = await lookup("acme/web")
    assert api["env_keys_set"] == ["DB_URL", "SHARED"] and web["env_keys_set"] == ["SHARED"]
    assert env.global_reads == 1, "the global names are read once per run"
    assert "secret" not in repr(api) + repr(web)
