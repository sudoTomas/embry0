"""Discovery turns a forge listing plus a few file reads into facts, never guesses."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from embry0.execution.forge_client import Listing
from embry0.services.org_discovery import (
    RepoFacts,
    count_gap,
    discover,
    env_keys,
    hook_hosts,
    hook_points_here,
    hook_state,
    manifest_facts,
    scan_repository,
    summarize,
    ui_evidence,
)


class _Resp:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _file(text: str) -> _Resp:
    return _Resp(200, {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()})


class FakeForge:
    def __init__(self, routes: dict[str, _Resp], listing: list[dict[str, Any]]) -> None:
        self.routes = routes
        self.listing = listing
        self.asked: list[str] = []

    async def get(self, path: str, **_: Any) -> _Resp:
        self.asked.append(path)
        return self.routes.get(path, _Resp(404))

    async def paginate_listing(self, path: str, **_: Any) -> Listing:
        self.asked.append(path)
        return Listing(self.listing, complete=True)


WEB = {
    "name": "web",
    "full_name": "acme/web",
    "default_branch": "main",
    "language": "TypeScript",
    "pushed_at": "2026-10-15T10:00:00Z",
    "open_issues_count": 4,
    "private": True,
}
OLD = {"name": "old", "full_name": "acme/old", "archived": True, "default_branch": "master"}
API = {"name": "api", "full_name": "acme/api", "default_branch": "main", "language": "Java"}


def forge() -> FakeForge:
    pkg = json.dumps({"scripts": {"dev": "vite", "test": "vitest"}, "dependencies": {"react": "^19"}})
    return FakeForge(
        {
            "/repos/acme/web/contents/": _Resp(
                200,
                [
                    {"name": "package.json", "type": "file"},
                    {"name": ".env.example", "type": "file"},
                    {"name": "__tests__", "type": "dir"},
                    {"name": ".github", "type": "dir"},
                ],
            ),
            "/repos/acme/web/contents/package.json": _file(pkg),
            "/repos/acme/web/contents/.env.example": _file(
                "# comment\nAPI_URL=http://x\nexport TOKEN=secret\nAPI_URL=again\n"
            ),
            "/repos/acme/web/hooks": _Resp(200, [{"config": {"url": "https://embry0.example.com/api/v1/webhook"}}]),
            "/repos/acme/api/contents/": _Resp(
                200,
                [
                    {"name": "pom.xml", "type": "file"},
                    {"name": "docker-compose.yml", "type": "file"},
                    {"name": "src", "type": "dir"},
                ],
            ),
            "/repos/acme/api/hooks": _Resp(403),
        },
        [WEB, OLD, API],
    )


def test_env_keys_are_names_in_order_without_values_or_duplicates() -> None:
    assert env_keys("# c\nA=1\nexport B=2\nA=3\n  C = x\n") == ["A", "B", "C"]


def test_manifest_facts_find_a_boot_hint_and_a_frontend() -> None:
    facts = manifest_facts(json.dumps({"scripts": {"start": "node server.js"}, "devDependencies": {"vite": "7"}}))
    assert facts == {"boot_hint": "npm run start", "frontend": True, "has_tests_script": False}
    assert manifest_facts("not json") == {}


def test_hook_points_here_matches_only_this_deployment() -> None:
    hooks = [
        {"config": {"url": "https://other.example/hook"}},
        {"config": {"url": "https://embry0.example.com/api/v1/webhook"}},
    ]
    assert hook_points_here(hooks, ("https://embry0.example.com",)) is True
    assert hook_points_here(hooks, ("https://nowhere.example",)) is False
    assert hook_points_here(hooks, ("",)) is False


@pytest.mark.asyncio
async def test_scan_reads_manifest_env_and_hooks() -> None:
    f = forge()
    facts = await scan_repository(f, "acme", WEB, hook_origins=("https://embry0.example.com",))
    assert facts.repo == "acme/web" and facts.private is True and facts.open_issues == 4
    assert facts.pushed_at is not None and facts.pushed_at.year == 2026
    d = facts.facts
    assert d["frontend"] is True and d["boot_hint"] == "npm run dev" and d["has_tests_script"] is True
    assert d["env_keys"] == ["API_URL", "TOKEN"]
    assert d["tests_dir"] == "__tests__" and d["ci"] is True
    assert d["hooks_visible"] is True and d["hook_present"] is True


@pytest.mark.asyncio
async def test_scan_marks_what_the_token_cannot_see_as_none_and_skips_archived() -> None:
    f = forge()
    api = await scan_repository(f, "acme", API, hook_origins=())
    assert api.facts["hooks_visible"] is False and api.facts["hook_present"] is None
    assert api.facts["boot_hint"] == "docker compose -f docker-compose.yml up"
    assert api.facts["manifests"] == ["pom.xml"] and api.facts["frontend"] is False
    old = await scan_repository(f, "acme", OLD, hook_origins=())
    assert old.archived is True and old.facts == {"skipped": "archived"}
    assert not any(p.startswith("/repos/acme/old/") for p in f.asked)


@pytest.mark.asyncio
async def test_discover_folds_in_embry0_state_and_reports_progress() -> None:
    f = forge()
    seen: list[tuple[int, int]] = []

    async def state(repo: str) -> dict[str, Any]:
        return {
            "registered": repo == "acme/api",
            "qa_config": False,
            "watcher_stack": repo == "acme/api",
            "tracker_mapped": False,
            "active": repo == "acme/api",
        }

    async def progress(i: int, n: int) -> None:
        seen.append((i, n))

    found = await discover(
        f,
        "acme",
        owner_kind="organization",
        hook_origins=("https://embry0.example.com",),
        embry0_state=state,
        progress=progress,
    )
    assert found.complete is True
    out = found.repos
    assert [o.repo for o in out] == ["acme/web", "acme/old", "acme/api"]
    assert f.asked[0] == "/orgs/acme/repos"
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert out[2].facts["registered"] is True and out[2].facts["watcher_stack"] is True
    assert "registered" not in out[1].facts
    s = summarize(out)
    assert s == {
        "repos": 3,
        "archived": 1,
        "registered": 1,
        "hooked": 1,
        "hooks_not_visible": 1,
        "hooks_unknown": 0,
        "qa_config": 0,
        "watched": 1,
        "tracker_mapped": 0,
        "frontend": 1,
        "boot_hint": 2,
    }


# ---- recorded payloads: a static site and a Python app with a public/ folder ----

# A GitHub repository record whose language is HTML (a static site, no manifest).
SITE = {
    "name": "site",
    "full_name": "acme/site",
    "default_branch": "main",
    "language": "HTML",
    "pushed_at": "2026-10-10T10:00:00Z",
    "private": False,
    "fork": False,
    "visibility": "public",
}
# A Python service that serves a built UI from public/index.html.
PORTAL = {
    "name": "portal",
    "full_name": "acme/portal",
    "default_branch": "main",
    "language": "Python",
    "private": True,
    "visibility": "internal",
}
HOOKS_PAYLOAD = [
    {
        "type": "Repository",
        "id": 501,
        "name": "web",
        "active": True,
        "events": ["issues", "pull_request"],
        "config": {"content_type": "json", "insecure_ssl": "0", "url": "https://hooks.example.net/api/v1/webhook"},
    },
    {
        "type": "Repository",
        "id": 502,
        "name": "web",
        "active": True,
        "events": ["push"],
        "config": {"content_type": "form", "url": "https://ci.example.org/github"},
    },
]


def forge_with_sites() -> FakeForge:
    return FakeForge(
        {
            "/repos/acme/site/contents/": _Resp(
                200, [{"name": "index.html", "type": "file"}, {"name": "css", "type": "dir"}]
            ),
            "/repos/acme/site/hooks": _Resp(200, HOOKS_PAYLOAD),
            "/repos/acme/portal/contents/": _Resp(
                200,
                [
                    {"name": "pyproject.toml", "type": "file"},
                    {"name": "public", "type": "dir"},
                    {"name": "app", "type": "dir"},
                ],
            ),
            "/repos/acme/portal/contents/public": _Resp(
                200, [{"name": "index.html", "type": "file"}, {"name": "assets", "type": "dir"}]
            ),
            "/repos/acme/portal/hooks": _Resp(200, HOOKS_PAYLOAD),
        },
        [SITE, PORTAL],
    )


def test_ui_evidence_prefers_the_strongest_sign() -> None:
    assert ui_evidence(language="Python", names={}, frontend_dependency=True, static_index=None) == "dependency"
    assert ui_evidence(language="HTML", names={}, frontend_dependency=False, static_index=None) == "language"
    assert (
        ui_evidence(language="Go", names={"index.html": "file"}, frontend_dependency=False, static_index=None)
        == "index.html"
    )
    assert (
        ui_evidence(language="Python", names={}, frontend_dependency=False, static_index="static/index.html")
        == "static/index.html"
    )
    assert ui_evidence(language="Java", names={"pom.xml": "file"}, frontend_dependency=False, static_index=None) is None


@pytest.mark.asyncio
async def test_a_static_site_and_a_public_index_count_as_a_ui() -> None:
    f = forge_with_sites()
    site = await scan_repository(f, "acme", SITE, hook_origins=("https://hooks.example.net",))
    assert site.facts["frontend"] is True and site.facts["ui_evidence"] == "language"
    assert site.facts["manifests"] == []
    portal = await scan_repository(f, "acme", PORTAL, hook_origins=("https://hooks.example.net",))
    assert portal.facts["frontend"] is True and portal.facts["ui_evidence"] == "public/index.html"
    assert portal.internal is True and portal.private is True
    assert "/repos/acme/portal/contents/public" in f.asked
    # The static directories are only listed when nothing else says UI.
    assert not any(p.startswith("/repos/acme/site/contents/css") for p in f.asked)


@pytest.mark.asyncio
async def test_hooks_compare_against_the_webhook_url() -> None:
    f = forge_with_sites()
    seen = await scan_repository(f, "acme", SITE, hook_origins=("https://hooks.example.net",))
    assert seen.facts["hook_present"] is True and seen.facts["hook_count"] == 2
    assert seen.facts["hook_hosts"] == ["ci.example.org", "hooks.example.net"]
    missed = await scan_repository(f, "acme", SITE, hook_origins=("https://embry0.example.com",))
    assert missed.facts["hook_present"] is False and missed.facts["hook_count"] == 2


@pytest.mark.asyncio
async def test_hooks_without_a_webhook_url_are_unknown_not_absent() -> None:
    """Hooks can point at a webhooks hostname configured outside the
    repository, so reading them as absent would report hooked 0 while hooks
    exist. Unless WEBHOOK_PUBLIC_URL names it, a repository's hooks are
    unknown, and the needs list asks for the setting."""
    f = forge_with_sites()
    facts = await scan_repository(f, "acme", SITE, hook_origins=())
    assert facts.facts["hooks_visible"] is True
    assert facts.facts["hook_present"] is None
    assert facts.facts["hook_count"] == 2
    named = await scan_repository(f, "acme", SITE, hook_origins=("https://hooks.example.net",))
    assert named.facts["hook_present"] is True
    f.routes["/repos/acme/site/hooks"] = _Resp(200, [])
    bare = await scan_repository(f, "acme", SITE, hook_origins=())
    assert bare.facts["hook_present"] is False, "no hooks at all is absent whatever the setting"
    assert bare.facts["hook_count"] == 0 and bare.facts["hook_hosts"] == []


def test_hook_state_and_hosts() -> None:
    ours = {"config": {"url": "https://hooks.example.net/api/v1/webhook?token=abc"}}
    other = {"config": {"url": "https://ci.example.org/hook?secret=xyz"}}
    url = ("https://hooks.example.net",)
    assert hook_state([ours, other], url) is True
    assert hook_state([other], url) is False
    assert hook_state([other], ()) is None, "no webhook URL to compare against: unknown"
    assert hook_state([ours], ("",)) is None
    assert hook_state([], ()) is False
    assert hook_state(["junk"], ()) is False
    assert hook_hosts([ours, other, ours, {"config": {}}, "junk"]) == ["ci.example.org", "hooks.example.net"]
    assert not any("token" in h or "secret" in h for h in hook_hosts([ours, other])), "hosts only, never a query"


def test_count_gap_explains_what_the_numbers_allow() -> None:
    facts = [
        RepoFacts(owner="acme", repo="acme/web", private=True),
        RepoFacts(owner="acme", repo="acme/api", private=True, internal=True),
        RepoFacts(owner="acme", repo="acme/site"),
        RepoFacts(owner="acme", repo="acme/old", archived=True, fork=True),
    ]
    agree = count_gap(facts, 4)
    assert agree["gap"] == 0 and agree["explained_by"] == "the owner record and the listing agree"
    counted = (agree["public"], agree["private"], agree["internal"], agree["forks"], agree["archived"])
    assert counted == (2, 1, 1, 1, 1), "public, private and internal are disjoint"
    public_only = count_gap(facts, 2)
    assert public_only["gap"] == -2 and "public repositories only" in public_only["explained_by"]
    no_internal = count_gap(facts, 3)
    assert no_internal["explained_by"] == "the owner record does not count internal repositories"
    doubled = count_gap(facts, 6)
    assert doubled["gap"] == 2 and "counts private repositories twice" in doubled["explained_by"]
    unseen = count_gap(facts, 7)
    assert unseen["gap"] == 3 and unseen["explained_by"] is None
    assert "count_gap" not in summarize(facts)
    assert summarize(facts, owner_says=7)["count_gap"] == unseen


def test_count_gap_reads_forks_and_archived_apart() -> None:
    facts = [
        RepoFacts(owner="acme", repo="acme/a"),
        RepoFacts(owner="acme", repo="acme/b", fork=True),
        RepoFacts(owner="acme", repo="acme/c", fork=True),
        RepoFacts(owner="acme", repo="acme/d", archived=True),
    ]
    assert count_gap(facts, 2)["explained_by"] == "the owner record does not count forks"
    assert count_gap(facts, 3)["explained_by"] == "the owner record does not count archived repositories"
    assert count_gap(facts, 1)["explained_by"] == "the owner record counts neither forks nor archived repositories"
    assert count_gap(facts, 9)["explained_by"] is None


@pytest.mark.asyncio
async def test_a_page_the_forge_refuses_fails_the_listing_instead_of_shortening_it() -> None:
    """ADR 0014: a listing cut short by a refused page (a revoked token, a
    rate limit) would read as repositories gone from the owner, and the needs
    list would propose deactivating them. The run fails instead."""
    import httpx

    from embry0.execution.github_client import GitHubClient
    from embry0.services.org_discovery import list_repositories

    second = "https://api.github.com/orgs/acme/repos?page=2"
    pages = [
        httpx.Response(
            200,
            json=[{"full_name": "acme/web"}],
            headers={"Link": f'<{second}>; rel="next"'},
            request=httpx.Request("GET", "https://api.github.com/orgs/acme/repos"),
        ),
        httpx.Response(403, json={"message": "API rate limit exceeded"}, request=httpx.Request("GET", second)),
    ]

    async def get(path: str, **kwargs: Any) -> httpx.Response:
        return pages.pop(0)

    client = GitHubClient(default_token="t", max_attempts=1)
    client.get = get  # type: ignore[method-assign]
    with pytest.raises(httpx.HTTPStatusError):
        await list_repositories(client, "acme", "organization")
    assert pages == [], "both pages were asked for"


@pytest.mark.asyncio
@pytest.mark.parametrize(("pages", "complete"), [(3, True), (25, False)])
async def test_the_listing_says_when_the_page_cap_cut_it_short(pages: int, complete: bool) -> None:
    """An owner past 20 pages of 100 is listed up to the cap, and the
    listing says it is not whole: a repository it did not reach is still
    there, so nothing may read it as gone (ADR 0014)."""
    import httpx

    from embry0.execution.github_client import GitHubClient
    from embry0.services.org_discovery import LISTING_MAX_PAGES, list_repositories

    asked: list[str] = []

    async def get(path: str, **kwargs: Any) -> httpx.Response:
        asked.append(path)
        url = httpx.URL(path if path.startswith("http") else f"https://api.github.com{path}")
        page = int(url.params.get("page", "1"))
        headers = (
            {"Link": f'<https://api.github.com/orgs/acme/repos?page={page + 1}>; rel="next"'} if page < pages else {}
        )
        return httpx.Response(200, json=[{"full_name": f"acme/r{page:02d}"}], headers=headers)

    client = GitHubClient(default_token="t", max_attempts=1)
    client.get = get  # type: ignore[method-assign]
    listing = await list_repositories(client, "acme", "organization")
    assert listing.complete is complete
    assert len(asked) == min(pages, LISTING_MAX_PAGES) == len(listing.items)


@pytest.mark.asyncio
async def test_a_direct_read_that_fails_or_says_nothing_is_unknown() -> None:
    """confirm_gone answers None, not gone, when the read raises (a transport
    failure after the retries) or the forge answers 200 without a record."""
    from embry0.services.org_discovery import confirm_gone

    class Broken:
        async def get(self, path: str, **_: Any) -> Any:
            raise OSError("connection reset")

    assert await confirm_gone(Broken(), "acme/web") is None  # type: ignore[arg-type]
    assert (
        await confirm_gone(FakeForge({"/repos/acme/web": _Resp(200, ["not", "a", "record"])}, []), "acme/web") is None
    )
    assert await confirm_gone(FakeForge({"/repos/acme/web": _Resp(200, {"full_name": ""})}, []), "acme/web") is None
