"""Areas of a monorepo: the file that names them, which area a path, a log or a label is in, and a title naming it."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from embry0.services.areas import (
    AREAS_PATH,
    CACHE_SECONDS,
    Area,
    AreaReader,
    area_of_change,
    area_of_labels,
    area_of_logs,
    area_of_path,
    area_of_paths,
    parse_areas,
    read_contents,
    titled,
)

FILE = """
areas:
  - name: Billing
    paths: ["services/billing/**", "web/apps/billing/", "services/billing-app/*.xml"]
    logs: billing-api
    owners: ["@octo", "not a login!", "hubot"]
  - name: Search
    paths: ["services/search/**"]
  - paths: ["nameless/**"]
  - name: "  "
  - Plain string
"""


class _Resp:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        return self._body


def test_the_file_names_areas_with_paths_logs_and_owners() -> None:
    billing, search = parse_areas(FILE)
    assert billing == Area(
        "Billing",
        ("services/billing/**", "web/apps/billing/", "services/billing-app/*.xml"),
        ("billing-api",),
        ("octo", "hubot"),
    )
    assert search.paths == ("services/search/**",) and search.owners == ()
    assert billing.slug == "billing" and Area("Customer Portals").slug == "customer-portals"


@pytest.mark.parametrize("text", [None, "", "areas: [", "- a list", "areas: nope"])
def test_a_missing_or_broken_file_names_no_areas(text: str | None) -> None:
    assert parse_areas(text) == []


def test_paths_match_their_globs() -> None:
    areas = parse_areas(FILE)
    assert area_of_path(areas, "services/billing/src/Invoice.java").name == "Billing"  # type: ignore[union-attr]
    assert area_of_path(areas, "/web/apps/billing/src/main.tsx").name == "Billing"  # type: ignore[union-attr]
    assert area_of_path(areas, "services/billing-app/pom.xml").name == "Billing"  # type: ignore[union-attr]
    assert area_of_path(areas, "services/billing-app/sub/pom.xml") is None, "* stays within one directory"
    assert area_of_path(areas, "services/billingx/a.java") is None
    assert area_of_path(areas, "README.md") is None
    assert area_of_path([Area("Docs", ("**/*.md",))], "README.md").name == "Docs"  # type: ignore[union-attr]
    assert area_of_path([Area("Docs", ("**/*.md",))], "a/b/c.md").name == "Docs"  # type: ignore[union-attr]


def test_a_change_is_in_the_area_most_of_its_files_are() -> None:
    areas = parse_areas(FILE)
    files = ["services/search/A.java", "services/billing/B.java", "services/billing/C.java", "docs/x.md"]
    assert area_of_paths(areas, files).name == "Billing"  # type: ignore[union-attr]
    assert area_of_paths(areas, ["docs/x.md"]) is None


def test_a_log_or_a_label_names_its_area() -> None:
    areas = parse_areas(FILE)
    assert area_of_logs(areas, "ERROR shop-BILLING-API-7f9c NullPointer", None).name == "Billing"  # type: ignore[union-attr]
    assert area_of_logs(areas, None, "") is None
    assert area_of_logs(areas, "shop-search timeout") is None
    assert area_of_labels(areas, ["bug", "area:billing"]).name == "Billing"  # type: ignore[union-attr]
    assert area_of_labels(areas, ["Search"]).name == "Search"  # type: ignore[union-attr]
    assert area_of_labels(areas, ["bug"]) is None


def test_a_title_names_its_area_unless_its_scope_does() -> None:
    billing = Area("Billing")
    assert titled("Fix the invoice sender", billing) == "[Billing] Fix the invoice sender"
    assert titled("fix(billing): the invoice sender", billing) == "fix(billing): the invoice sender"
    assert titled("fix(billing-app)!: drop it", billing) == "fix(billing-app)!: drop it"
    assert titled("fix(search): x", billing) == "[Billing] fix(search): x"
    assert titled("Fix it", None) == "Fix it"


@pytest.mark.asyncio
async def test_the_file_is_read_through_the_contents_api_and_kept() -> None:
    asked: list[str] = []

    async def get(url: str) -> _Resp:
        asked.append(url)
        if url.endswith(AREAS_PATH):
            return _Resp(200, {"content": base64.b64encode(FILE.encode()).decode()})
        return _Resp(404, {"message": "Not Found"})

    assert await read_contents(get, "o/r", "nothing.yml") is None
    now = [0.0]
    reader = AreaReader(clock=lambda: now[0])

    async def read(repo: str, path: str) -> str | None:
        return await read_contents(get, repo, path)

    assert [a.name for a in await reader.areas("O/R", read)] == ["Billing", "Search"]
    await reader.areas("o/r", read)
    assert asked.count(f"/repos/O/R/contents/{AREAS_PATH}") == 1, "a repository's areas are kept"
    now[0] += CACHE_SECONDS
    await reader.areas("o/r", read)
    assert asked.count(f"/repos/o/r/contents/{AREAS_PATH}") == 1, "and read again once they are old"

    async def broken(repo: str, path: str) -> str | None:
        raise RuntimeError("forge down")

    assert await AreaReader().areas("o/r", broken) == []


@pytest.mark.asyncio
async def test_a_branch_is_in_the_area_of_the_files_it_changes() -> None:
    asked: list[str] = []

    async def get(url: str) -> _Resp:
        asked.append(url)
        if "/contents/" in url:
            return _Resp(200, {"content": base64.b64encode(FILE.encode()).decode()})
        return _Resp(200, {"files": [{"filename": "services/billing/A.java"}, {"filename": "docs/b.md"}, "junk"]})

    area = await area_of_change(get, "o/r", "main", "embry0/iss-1/fix", AreaReader())
    assert area is not None and area.name == "Billing"
    assert asked[-1] == "/repos/o/r/compare/main...embry0%2Fiss-1%2Ffix"

    async def no_areas(url: str) -> _Resp:
        asked.append(url)
        return _Resp(404)

    asked.clear()
    assert await area_of_change(no_areas, "o/r", "main", "b", AreaReader()) is None
    assert len(asked) == 1, "a repository without areas is not asked for the compare"
