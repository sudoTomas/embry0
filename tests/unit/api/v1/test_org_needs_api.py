"""Organization needs API (ADR 0011): the list, dismiss and reopen."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.org_discovery import Discovery, RepoFacts
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

    async def fake_discover(client: Any, owner: str, **kw: Any) -> Discovery:
        return Discovery(
            [
                RepoFacts(
                    owner=owner,
                    repo="acme/new",
                    facts={"root_visible": True, "hooks_visible": True, "hook_present": True, "registered": False},
                )
            ]
        )

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

    async def fake_discover(client: Any, owner: str, **kw: Any) -> Discovery:
        seen.update(kw)
        return Discovery([])

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


# ---- a vanished repository is confirmed with the forge, one by one (ADR 0016) ----

GITHUB = "https://api.github.com"


class ListingForge:
    """GitHub as a discovery run reads it, behind ``GitHubClient.get``: the
    owner's listing page by page, following ``Link: rel="next"``, and one
    record per repository on a direct read (404 unless ``direct`` names it).
    ``endless`` keeps a next link on every page, so the listing outlasts the
    page cap. Every path asked is kept."""

    def __init__(
        self, pages: list[list[str]], direct: dict[str, httpx.Response] | None = None, *, endless: bool = False
    ) -> None:
        self.pages, self.direct, self.endless = pages, dict(direct or {}), endless
        self.asked: list[str] = []

    async def get(self, path: str, **_: Any) -> httpx.Response:
        self.asked.append(path)
        url = httpx.URL(path if path.startswith("http") else f"{GITHUB}{path}")
        request = httpx.Request("GET", url)
        if url.path.endswith("/acme/repos"):
            page = int(url.params.get("page", "1"))
            names = self.pages[page - 1] if page <= len(self.pages) else []
            headers = {}
            if self.endless or page < len(self.pages):
                headers["Link"] = f'<{GITHUB}{url.path}?page={page + 1}>; rel="next"'
            body = [{"full_name": n, "name": n.split("/")[1]} for n in names]
            return httpx.Response(200, json=body, headers=headers, request=request)
        response = self.direct.get(url.path.removeprefix("/repos/"))
        if response is not None:
            response.request = request
            return response
        return httpx.Response(404, json={"message": "Not Found"}, request=request)

    def confirmed(self) -> list[str]:
        """The repositories read directly, one ``GET /repos/{owner}/{name}`` each."""
        return [p.removeprefix("/repos/") for p in self.asked if p.startswith("/repos/") and p.count("/") == 3]


class HeldRegistry:
    """The registry rows the state lookup reads: each one held active."""

    def __init__(self, held: set[str]) -> None:
        self.held = held

    async def get(self, repo: str) -> dict[str, Any] | None:
        return {"repo": repo, "active": True} if repo in self.held else None


async def _rediscover(
    app: Any, monkeypatch: pytest.MonkeyPatch, forge: ListingForge, held: set[str]
) -> tuple[dict[str, Any], list[str] | None]:
    """One discovery run against ``forge``, through POST /orgs/{owner}/discover
    and the real discover, listing and paginate: the run row, and the
    repositories the open repo_vanished need proposes (None without one)."""
    import asyncio

    from embry0.execution.github_client import GitHubClient

    monkeypatch.setattr(GitHubClient, "get", forge.get)
    app.state.repos_repo = HeldRegistry(held)
    await app.state.organizations_repo.update_access("acme", {"owner_kind": "organization"})
    async with _client(app) as client:
        started = await client.post("/api/v1/orgs/acme/discover")
        assert started.status_code == 202, started.text
        for _ in range(500):
            runs = (await client.get("/api/v1/orgs/acme/runs")).json()["runs"]
            if runs[0]["id"] == started.json()["id"] and runs[0]["status"] != "running":
                break
            await asyncio.sleep(0.01)
        needs = (await client.get("/api/v1/orgs/acme/needs")).json()["needs"]
    assert runs[0]["status"] == "done", runs[0]
    vanished = [n["repos"] for n in needs if n["kind"] == "repo_vanished" and n["status"] == "open"]
    return runs[0], (vanished[0] if vanished else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("full_name", ["acme/web", "Acme/Web"])
async def test_a_repository_the_listing_skipped_but_the_forge_still_has_is_not_vanished(
    app: Any, monkeypatch: pytest.MonkeyPatch, full_name: str
) -> None:
    """Pagination drift: the listing pages over newest push first, so a push
    between two page reads moves a repository onto a page already read. The
    listing misses acme/web, an active registration; a direct read answers
    with it, the case aside, so nothing proposes deactivating it."""
    forge = ListingForge([["acme/api"]], {"acme/web": httpx.Response(200, json={"full_name": full_name})})
    run, vanished = await _rediscover(app, monkeypatch, forge, {"acme/web"})
    assert vanished is None, "the forge still has acme/web"
    assert forge.confirmed() == ["acme/web"], "the candidate is read directly before it counts"
    assert run["summary"]["vanished"] == 1 and run["summary"]["vanished_confirmed"] == 0


@pytest.mark.asyncio
async def test_a_repository_the_forge_answers_404_for_is_vanished(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    forge = ListingForge([["acme/api"]])
    run, vanished = await _rediscover(app, monkeypatch, forge, {"acme/web"})
    assert forge.confirmed() == ["acme/web"]
    assert vanished == ["acme/web"]
    assert run["summary"]["vanished_confirmed"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"full_name": "acme/web-app"}),
        httpx.Response(200, json={"full_name": "other/web"}),
        httpx.Response(301, headers={"Location": f"{GITHUB}/repositories/42"}),
    ],
    ids=["renamed", "transferred", "redirected"],
)
async def test_a_repository_now_under_another_name_is_vanished(
    app: Any, monkeypatch: pytest.MonkeyPatch, answer: httpx.Response
) -> None:
    """Renamed or transferred: the forge answers the old name with the
    repository under its new one, or redirects it (GitHub's 301)."""
    forge = ListingForge([["acme/api"]], {"acme/web": answer})
    _run, vanished = await _rediscover(app, monkeypatch, forge, {"acme/web"})
    assert forge.confirmed() == ["acme/web"]
    assert vanished == ["acme/web"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 403])
async def test_a_repository_the_forge_cannot_answer_for_is_not_vanished(
    app: Any, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """A 5xx or a rate limit on the direct read is unknown, and unknown is
    not gone: a flaky forge never proposes a deactivation."""
    forge = ListingForge([["acme/api"]], {"acme/web": httpx.Response(status, json={"message": "no"})})
    run, vanished = await _rediscover(app, monkeypatch, forge, {"acme/web"})
    assert vanished is None, "unknown is not gone"
    assert forge.confirmed() == ["acme/web"]
    assert run["status"] == "done" and run["summary"]["vanished_confirmed"] == 0


@pytest.mark.asyncio
async def test_a_listing_the_page_cap_cut_short_proposes_nothing(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """An owner past the cap (20 pages): the listing stops with a next page
    unread, so a repository it did not reach is still there. Nothing is read
    directly and nothing is proposed; the summary and a log line say why."""
    import structlog

    pages = [["acme/api"], *[[f"acme/r{page:02d}"] for page in range(2, 21)]]
    forge = ListingForge(pages, endless=True)
    with structlog.testing.capture_logs() as logs:
        run, vanished = await _rediscover(app, monkeypatch, forge, {"acme/web"})
    assert sum(1 for p in forge.asked if "/acme/repos" in p) == 20, "the listing read up to the cap"
    assert forge.confirmed() == [], "a truncated listing confirms nothing"
    assert vanished is None
    assert run["summary"]["listing_complete"] is False and run["summary"]["vanished_confirmed"] == 0
    skipped = [e for e in logs if e["event"] == "org_vanished_unchecked"]
    assert [e["reason"] for e in skipped] == ["listing_truncated"], logs


@pytest.mark.asyncio
@pytest.mark.parametrize("held", [50, 51])
async def test_more_vanished_registrations_than_a_run_confirms_propose_nothing(
    app: Any, monkeypatch: pytest.MonkeyPatch, held: int
) -> None:
    """A run reads at most 50 candidates directly. Up to that, each is
    confirmed; past it, none is read and nothing is proposed, with a log
    line: that many registrations leaving at once is a credential's sight
    narrowing more likely than deletions."""
    import structlog

    gone = {"acme/web", *(f"acme/k{i:02d}" for i in range(1, held))}
    first = (await app.state.org_repos_repo.list_runs("acme"))[0]["id"]
    for repo in sorted(gone - {"acme/web"}):
        await app.state.org_repos_repo.upsert_fact(
            RepoFacts(owner="acme", repo=repo, facts={"root_visible": True, "registered": True}), run_id=first
        )
    forge = ListingForge([["acme/api"]])
    with structlog.testing.capture_logs() as logs:
        run, vanished = await _rediscover(app, monkeypatch, forge, gone)
    skipped = [e for e in logs if e["event"] == "org_vanished_unchecked"]
    if held == 50:
        assert sorted(forge.confirmed()) == sorted(gone)
        assert vanished == sorted(gone)
        assert skipped == []
    else:
        assert forge.confirmed() == [], "past the limit nothing is read"
        assert vanished is None
        assert [(e["reason"], e["candidates"], e["limit"]) for e in skipped] == [("over_confirm_limit", 51, 50)]
    assert run["summary"]["vanished"] == held
