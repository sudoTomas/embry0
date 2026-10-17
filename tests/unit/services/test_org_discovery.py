"""Discovery turns a forge listing plus a few file reads into facts, never guesses."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from embry0.services.org_discovery import (
    discover,
    env_keys,
    hook_points_here,
    manifest_facts,
    scan_repository,
    summarize,
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

    async def paginate(self, path: str, **_: Any) -> list[Any]:
        self.asked.append(path)
        return self.listing


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

    out = await discover(
        f,
        "acme",
        owner_kind="organization",
        hook_origins=("https://embry0.example.com",),
        embry0_state=state,
        progress=progress,
    )
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
        "qa_config": 0,
        "watched": 1,
        "tracker_mapped": 0,
        "frontend": 1,
        "boot_hint": 2,
    }
