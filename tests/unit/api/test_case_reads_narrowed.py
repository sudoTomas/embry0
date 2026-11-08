"""A key reads only the cases of its repositories: the list of cases, and what a case it reads links to.

On the real app over a real database (:mod:`tests.fixtures.case_reads`). The
list of cases narrows its query to the key's repositories before it pages,
counts and makes its cursor, so every page holds the key's own cases however
many newer cases other repositories have, with every filter, and its total
counts them alone; a case filed on an owner or on no repository is no
restricted key's. A case the key reads lists and counts only the jobs and pull
requests of its repositories, names no issue or case it does not read, and its
history leaves out what names them; the list and the case agree. A need and a
merge approval name no case the key does not read.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.case_reads import (
    CASE,
    CROSSING,
    HIDDEN_EVENTS,
    HIDDEN_PR,
    MERGE_SHA,
    NO_REPOSITORY,
    OWN_PR,
    OWNER_CASE,
    OWNER_NEED,
    PR,
    PR_OF_A_HIDDEN_JOB,
    SHOWN_EVENTS,
    UNNAMED_MERGE,
    UNNAMED_MERGE_SHA,
    UNNAMED_PR,
    case_reads_app,
)
from tests.fixtures.issue_reads import APPROVAL, ISSUE, JOB_ELSEWHERE, JOB_HERE, MIXED_HIDDEN, same_answer
from tests.fixtures.job_reads import JOB
from tests.fixtures.repository_writes import COVERED, NOW, OWNER, UNCOVERED, call

pytestmark = pytest.mark.requires_postgres

#: How many newer cases the repository the key does not cover has: more than a page holds.
NEWER = 70


async def _more_cases(db: DatabasePool) -> None:
    """Newer cases of the other repository, of every stage and origin a filter selects, and of no repository."""
    stages = ("detected", "tracked", "merged", "closed")
    for i in range(NEWER):
        await db.execute(
            "INSERT INTO healing_cases (id, repo, title, origin, stage, tracker, tracker_key, resolution_path, "
            "created_at, detected_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)",
            f"hc-newer-{i:03d}",
            "" if i % 10 == 9 else (OWNER[UNCOVERED] if i % 10 == 8 else UNCOVERED),
            f"Newer {i}",
            ("watcher", "alert", "org")[i % 3],
            stages[i % 4],
            "github" if i % 2 else None,
            str(1000 + i) if i % 2 else None,
            "cancelled" if stages[i % 4] == "closed" else None,
            NOW - timedelta(seconds=60 - i * 0.5),
        )


async def _pages(app: Any, token: str, query: str, limit: int) -> tuple[list[tuple[str, str]], set[int]]:
    """Every case ``GET /cases`` lists to ``token`` with ``query``, ``limit`` a page, walked by its cursor, and the
    totals the pages report."""
    seen: list[tuple[str, str]] = []
    totals: set[int] = set()
    cursor = ""
    for _ in range(500):
        response = await call(app, "GET", f"/api/v1/cases?limit={limit}{query}{cursor}", token)
        assert response.status_code == 200, response.text
        body = response.json()
        totals.add(body["total"])
        seen += [(c["id"], c["repo"]) for c in body["cases"]]
        if body["next_cursor"] is None:
            return seen, totals
        cursor = f"&cursor={body['next_cursor']}"
    raise AssertionError("the walk never ended")


#: Each filter the list takes.
QUERIES = [
    "",
    "&stage=tracked",
    "&stage=merged",
    "&origin=watcher",
    "&origin=org",
    "&origin=alert",
    "&resolution_path=cancelled",
    "&healed_verdict=pending",
    "&fix_deployed=false",
    "&open_only=true",
    "&awaiting_decision=true",
    f"&repo={UNCOVERED}",
    f"&repo={COVERED}",
    f"&repo={OWNER[UNCOVERED]}",
]


@pytest.mark.parametrize("query", QUERIES)
async def test_the_case_list_pages_and_counts_only_the_key_s_cases(
    query: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with case_reads_app(db, monkeypatch) as (app, keys, _values):
        await _more_cases(db)
        everyone, everyone_totals = await _pages(app, keys.open, query, 200)
        limited, limited_totals = await _pages(app, keys.limited, query, 1)
    own = [item for item in everyone if item[1] == COVERED]
    assert any(repo != COVERED for _id, repo in everyone) or query == f"&repo={COVERED}", (
        f"{query}: a key without a list is shown cases the limited key does not read, so the check measures something"
    )
    assert limited == own, f"{query}: the limited key's pages hold its own cases, each once, in order: {limited}"
    assert limited_totals == {len(own)}, f"{query}: every page's total counts the key's cases alone: {limited_totals}"
    assert everyone_totals == {len(everyone)}, everyone_totals


async def test_another_repository_s_cases_list_as_a_repository_with_none(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        other = await call(app, "GET", f"/api/v1/cases?repo={UNCOVERED}", keys.limited)
        nobody = await call(app, "GET", "/api/v1/cases?repo=zyxwvu/gizmos", keys.limited)
        reached = await call(app, "GET", f"/api/v1/cases?repo={UNCOVERED}", keys.open)
    differ = same_answer(other, {UNCOVERED: "<repo>"}, nobody, {"zyxwvu/gizmos": "<repo>"})
    assert differ is None, f"?repo= another repository answers as one with no cases: {differ}"
    assert reached.json()["total"] >= 1, reached.text


def _links(case: dict[str, Any]) -> dict[str, Any]:
    """What a case, or its row, names of the issue, the cases and the merge it links to, and its own state."""
    return {
        "issue_id": case["issue_id"],
        "parent_case_id": case["parent_case_id"],
        "caused_regression_case_id": case["caused_regression_case_id"],
        "merge_sha": case["merge_sha"],
        "fix_deployed": case["fix_deployed"],
        "stage": case["stage"],
        "merged_at": case["timestamps"]["merged_at"],
    }


def _case_view(body: dict[str, Any]) -> dict[str, Any]:
    """What a case names of what it links to, as ``GET /cases/{id}`` answers it."""
    return {
        **_links(body),
        "jobs": [job["job_id"] for job in body["jobs"]],
        "prs": [(pr["url"], pr["job_id"]) for pr in body["prs"]],
        "pr": body["pr"]["url"] if body["pr"] else None,
        "events": [event["detail"].get("why") for event in body["events"]],
    }


def _row_view(body: dict[str, Any], case_id: str) -> dict[str, Any]:
    """What a case's row names of what it links to, as ``GET /cases`` lists it."""
    row = next(case for case in body["cases"] if case["id"] == case_id)
    return {
        **_links(row),
        "jobs_count": row["jobs_count"],
        "prs_count": row["prs_count"],
        "pr": row["pr"]["url"] if row["pr"] else None,
    }


async def test_a_case_the_key_reads_names_nothing_of_another_repository(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The crossing case: no issue, case, job, pull request, merge or history of the other repository to the limited
    key, in the list and alone; its stage, its stage times and whether its fix is deployed are its own and stay."""
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        seen = {}
        for name, token in (("limited", keys.limited), ("open", keys.open)):
            detail = await call(app, "GET", f"/api/v1/cases/{CROSSING}", token)
            listed = await call(app, "GET", "/api/v1/cases?limit=200", token)
            assert detail.status_code == listed.status_code == 200, (detail.text, listed.text)
            seen[name] = (_case_view(detail.json()), _row_view(listed.json(), CROSSING))
    everyone, everyone_row = seen["open"]
    own_state = {key: everyone[key] for key in ("fix_deployed", "stage", "merged_at")}
    hidden_links = {
        "issue_id": None,
        "parent_case_id": None,
        "caused_regression_case_id": None,
        "merge_sha": None,
    }
    limited, limited_row = seen["limited"]
    assert limited == {
        **hidden_links,
        **own_state,
        "jobs": [JOB_HERE],
        "prs": [(OWN_PR, JOB_HERE), (PR_OF_A_HIDDEN_JOB, None)],
        "pr": OWN_PR,
        "events": list(SHOWN_EVENTS),
    }, f"the limited key reads no issue, case, job, pull request, merge or history of another repository: {limited}"
    assert limited_row == {**hidden_links, **own_state, "jobs_count": 1, "prs_count": 2, "pr": OWN_PR}, (
        f"the limited key's row of its case counts and names only what it reads: {limited_row}"
    )
    shown = {
        "issue_id": MIXED_HIDDEN,
        "parent_case_id": CASE[UNCOVERED],
        "caused_regression_case_id": NO_REPOSITORY,
        "merge_sha": MERGE_SHA,
    }
    assert (own_state["fix_deployed"], own_state["stage"]) == (True, "deployed") and own_state["merged_at"], own_state
    assert {key: everyone[key] for key in shown} == shown, everyone
    assert set(everyone["jobs"]) == {JOB_HERE, JOB_ELSEWHERE} and len(everyone["prs"]) == 4, everyone
    assert set(everyone["events"]) == set(SHOWN_EVENTS) | set(HIDDEN_EVENTS), everyone["events"]
    assert everyone_row == {**shown, **own_state, "jobs_count": 2, "prs_count": 4, "pr": HIDDEN_PR}, (
        f"a key without a list reads the case whole, so the check measures something: {everyone_row}"
    )
    for name in ("limited", "open"):
        view, row = seen[name]
        assert (row["jobs_count"], row["prs_count"], row["pr"]) == (len(view["jobs"]), len(view["prs"]), view["pr"]), (
            f"{name}: the list counts and picks what the case lists: {row} {view}"
        )


async def test_the_key_reads_its_own_case_whole(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        limited = await call(app, "GET", f"/api/v1/cases/{CASE[COVERED]}", keys.limited)
        everyone = await call(app, "GET", f"/api/v1/cases/{CASE[COVERED]}", keys.open)
    assert limited.status_code == 200, limited.text
    assert limited.json() == everyone.json(), "the key reads its own case as a key without a list does"
    view = _case_view(limited.json())
    assert (view["issue_id"], view["jobs"], view["prs"]) == (
        ISSUE[COVERED],
        [JOB[COVERED]],
        [(PR[COVERED], JOB[COVERED])],
    )
    assert len(view["events"]) == 1, view


async def test_a_need_names_no_case_the_key_does_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The covered owner's need covering two repositories filed its case on the owner, which the key does not read,
    though it reads the need narrowed to its repository; dismissing the need answers it the same way."""
    owner = OWNER[COVERED]

    def need_case(response: Any) -> Any:
        assert response.status_code == 200, response.text
        body = response.json()
        need = next(n for n in body["needs"] if n["id"] == OWNER_NEED) if "needs" in body else body
        return need["case_id"], need["repos"]

    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        limited = need_case(await call(app, "GET", f"/api/v1/orgs/{owner}/needs", keys.limited))
        everyone = need_case(await call(app, "GET", f"/api/v1/orgs/{owner}/needs", keys.open))
        dismissed = need_case(await call(app, "POST", f"/api/v1/orgs/{owner}/needs/{OWNER_NEED}/dismiss", keys.limited))
    assert limited == (None, [COVERED]), f"the limited key's need names no case filed on the owner: {limited}"
    assert everyone == (OWNER_CASE[owner], [COVERED, f"{owner}/unlisted"]), everyone
    assert dismissed == (None, [COVERED]), f"what dismissing the need answers names no case either: {dismissed}"


async def test_a_case_of_no_repository_is_no_restricted_key_s(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither listed nor read: a case filed on the covered owner, on the other owner, or on nothing."""
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        listed = await call(app, "GET", "/api/v1/cases?limit=200", keys.limited)
        everyone = await call(app, "GET", "/api/v1/cases?limit=200", keys.open)
        reads = {
            case_id: (await call(app, "GET", f"/api/v1/cases/{case_id}", keys.limited)).status_code
            for case_id in (*OWNER_CASE.values(), NO_REPOSITORY)
        }
    hidden = {*OWNER_CASE.values(), NO_REPOSITORY}
    assert not hidden & {case["id"] for case in listed.json()["cases"]}, listed.text
    assert hidden <= {case["id"] for case in everyone.json()["cases"]}, everyone.text
    assert reads == dict.fromkeys(hidden, 404), reads


async def test_a_merge_approval_names_no_case_the_key_does_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The covered repository's approval names the other repository's case: null to the key, in the list and alone."""
    approval = APPROVAL[COVERED]

    async def named(app: Any, token: str) -> tuple[Any, Any]:
        listed = await call(app, "GET", "/api/v1/merge-approvals", token)
        one = await call(app, "GET", f"/api/v1/merge-approvals/{approval}", token)
        assert listed.status_code == one.status_code == 200, (listed.text, one.text)
        row = next(item for item in listed.json()["items"] if item["id"] == approval)
        return row["case_id"], one.json()["case_id"]

    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        limited = await named(app, keys.limited)
        everyone = await named(app, keys.open)
    assert limited == (None, None), f"the key's approval names no case of {UNCOVERED}: {limited}"
    assert everyone == (CASE[UNCOVERED], CASE[UNCOVERED]), everyone


async def test_a_merge_only_an_address_naming_no_repository_carries_is_hidden(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pull request whose address names no repository is no key's with a list, so neither is the merge it carries:
    null to the limited key, in the list and alone, where a key without a list reads it."""
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        seen = {}
        for name, token in (("limited", keys.limited), ("open", keys.open)):
            detail = await call(app, "GET", f"/api/v1/cases/{UNNAMED_MERGE}", token)
            listed = await call(app, "GET", "/api/v1/cases?limit=200", token)
            assert detail.status_code == listed.status_code == 200, (detail.text, listed.text)
            seen[name] = (_case_view(detail.json()), _row_view(listed.json(), UNNAMED_MERGE))
    (limited, limited_row), (everyone, everyone_row) = seen["limited"], seen["open"]
    assert (limited["merge_sha"], limited_row["merge_sha"], limited["prs"]) == (None, None, []), (
        f"the limited key reads no merge of a pull request whose address names no repository: {limited} {limited_row}"
    )
    assert (everyone["merge_sha"], everyone_row["merge_sha"]) == (UNNAMED_MERGE_SHA, UNNAMED_MERGE_SHA), everyone
    assert everyone["prs"] == [(UNNAMED_PR, None)], everyone
    for key in ("stage", "merged_at", "fix_deployed"):
        assert limited[key] == everyone[key] and limited_row[key] == everyone_row[key], (key, limited, everyone)
