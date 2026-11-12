"""Areas of a monorepo: which area a path, a change, a log or a label is in, and a title naming it."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services.areas import (
    Area,
    area_of_change,
    area_of_labels,
    area_of_logs,
    area_of_path,
    area_of_paths,
    digest,
    slug_of,
    titled,
)

BILLING = Area(
    "Billing",
    ("services/billing/**", "web/apps/billing/", "services/billing-app/*.xml"),
    ("billing-api",),
    ("octo", "hubot"),
)
SEARCH = Area("Search", ("services/search/**",))
AREAS = [BILLING, SEARCH]


class _Resp:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        return self._body


def _name(area: Area | None) -> str | None:
    return area.name if area is not None else None


def test_an_area_s_slug_labels_its_work() -> None:
    assert BILLING.slug == "billing" and Area("Customer Portals").slug == "customer-portals"
    assert slug_of("  Web / App  ") == "web-app" and slug_of("!!!") == ""


def test_paths_match_their_globs() -> None:
    assert _name(area_of_path(AREAS, "services/billing/src/Invoice.java")) == "Billing"
    assert _name(area_of_path(AREAS, "/web/apps/billing/src/main.tsx")) == "Billing", "a trailing / is all below"
    assert _name(area_of_path(AREAS, "services/billing-app/pom.xml")) == "Billing"
    assert area_of_path(AREAS, "services/billing-app/sub/pom.xml") is None, "* stays within one directory"
    assert area_of_path(AREAS, "services/billingx/a.java") is None
    assert area_of_path(AREAS, "README.md") is None
    assert _name(area_of_path([Area("Docs", ("**/*.md",))], "README.md")) == "Docs"
    assert _name(area_of_path([Area("Docs", ("**/*.md",))], "a/b/c.md")) == "Docs"
    assert _name(area_of_path([Area("Cfg", ("conf/[ab]*.yml",))], "conf/app.yml")) == "Cfg", "qa.yaml's classes"
    route = [Area("Item", ("app/[[]id]/**",))]
    assert _name(area_of_path(route, "app/[id]/page.tsx")) == "Item" and area_of_path(route, "app/i/page.tsx") is None


def test_a_glob_this_release_cannot_read_matches_nothing() -> None:
    """A document a newer release wrote may hold a glob this one refuses: it matches nothing, never raises."""
    odd = Area("Odd", ("services/billing**", "a/[b"))
    assert area_of_path([odd, BILLING], "services/billing/a.java") == BILLING


def test_a_change_is_in_the_area_most_of_its_files_are() -> None:
    files = ["services/search/A.java", "services/billing/B.java", "services/billing/C.java", "docs/x.md"]
    assert _name(area_of_paths(AREAS, files)) == "Billing"
    assert area_of_paths(AREAS, ["docs/x.md"]) is None


def test_a_log_or_a_label_names_its_area() -> None:
    assert _name(area_of_logs(AREAS, "ERROR shop-BILLING-API-7f9c NullPointer", None)) == "Billing"
    assert area_of_logs(AREAS, None, "") is None
    assert area_of_logs(AREAS, "shop-search timeout") is None
    assert _name(area_of_labels(AREAS, ["bug", "area:billing"])) == "Billing"
    assert _name(area_of_labels(AREAS, ["Search"])) == "Search"
    assert area_of_labels(AREAS, ["bug"]) is None
    cafe = Area("Caf\u00e9")
    assert area_of_labels([cafe], ["area:CAFE\u0301"]) == cafe, "a label names its area however its accent is typed"
    assert area_of_labels([cafe], ["CAF\u00c9"]) == cafe


def test_a_title_names_its_area_unless_its_scope_does() -> None:
    assert titled("fix(платежи): x", Area("Платежи")) == "fix(платежи): x", "a scope in any script"
    assert titled("fix(search): x", Area("Платежи")) == "[Платежи] fix(search): x", "an area named in any script"
    billing = Area("Billing")
    assert titled("Fix the invoice sender", billing) == "[Billing] Fix the invoice sender"
    assert titled("fix(billing): the invoice sender", billing) == "fix(billing): the invoice sender"
    assert titled("fix(billing-app)!: drop it", billing) == "fix(billing-app)!: drop it"
    assert titled("fix(search): x", billing) == "[Billing] fix(search): x"
    assert titled("Fix it", None) == "Fix it"


def test_a_digest_moves_with_the_areas_and_their_order() -> None:
    assert digest(AREAS) == digest(list(AREAS))
    assert digest(AREAS) != digest([SEARCH, BILLING]), "the first matching area has a file, so order counts"
    assert digest(AREAS) != digest([BILLING, Area("Search", ("services/search/**",), ("search-api",))])


@pytest.mark.asyncio
async def test_a_branch_is_in_the_area_of_the_files_it_changes() -> None:
    asked: list[str] = []

    async def get(url: str) -> _Resp:
        asked.append(url)
        return _Resp(200, {"files": [{"filename": "services/billing/A.java"}, {"filename": "docs/b.md"}, "junk"]})

    area = await area_of_change(get, "o/r", "main", "embry0/iss-1/fix", AREAS)
    assert _name(area) == "Billing"
    assert asked == ["/repos/o/r/compare/main...embry0%2Fiss-1%2Ffix"]

    asked.clear()
    assert await area_of_change(get, "o/r", "main", "b", []) is None
    assert asked == [], "a repository without areas is not asked for the compare"

    async def unreadable(url: str) -> _Resp:
        return _Resp(404)

    assert await area_of_change(unreadable, "o/r", "main", "b", AREAS) is None
