"""The lists of issues, and what an issue or a job lists of the other, hold for a key with a repository allowlist only
what its repositories own.

On the real app over a real database (:mod:`tests.fixtures.issue_reads`). The
list of issues narrows its query to the key's repositories before it pages
and counts, so every page holds the key's own issues however many newer
issues other repositories have, with every filter, and its total counts them
alone. An issue's children, jobs and questions, and its counts of them, leave
out those of repositories the key does not cover; a job names no issue, and
lists no question about an issue, the key does not read. An issue with no
repository is no restricted key's. The merge approvals list holds the key's
own approvals at every limit.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from embry0.orchestration.dispatch_actors import REPO_ALLOWLIST
from embry0.storage.database import DatabasePool
from tests.fixtures.issue_reads import (
    CHILD,
    CHILD_ELSEWHERE,
    CHILD_HERE,
    INPUT,
    INPUT_ELSEWHERE,
    INPUT_HERE,
    ISSUE,
    JOB_ELSEWHERE,
    JOB_HERE,
    MIXED,
    MIXED_HIDDEN,
    issue_reads_app,
)
from tests.fixtures.job_reads import JOB
from tests.fixtures.repository_writes import COVERED, NOW, UNCOVERED, call

pytestmark = pytest.mark.requires_postgres

#: How many newer issues, and merge approvals, the repository the key does not cover has: more than a page holds.
NEWER = 70
#: What the newer issues' titles and bodies carry, as the key's own issues' do: the search filter's term.
TERM = "needle"


async def _more_issues(db: DatabasePool) -> None:
    for i in range(NEWER):
        await db.execute(
            "INSERT INTO issues (id, title, body, labels, repo, status, priority, created_at, updated_at) "
            "VALUES ($1, $2, $2, $3::jsonb, $4, $5, $6, $7, $7)",
            f"iss-newer-{i:03d}",
            f"Newer {TERM} {i}",
            ["bug", TERM],
            UNCOVERED,
            ("open", "closed", "triaging")[i % 3],
            ("high", "low")[i % 2],
            NOW - timedelta(seconds=60 - i * 0.5),
        )
    # The key's own issues carry the term too, each with a priority of its own, so every order lists them one way.
    await db.execute(
        "UPDATE issues SET title = title || $2, labels = labels || $3::jsonb, "
        "priority = CASE id WHEN $4 THEN 'high' WHEN $5 THEN 'medium' ELSE 'low' END WHERE repo = $1",
        COVERED,
        f" {TERM}",
        [TERM],
        ISSUE[COVERED],
        MIXED,
    )


async def _pages(app: Any, token: str, query: str, limit: int) -> tuple[list[tuple[str, str]], set[int]]:
    """Every issue ``GET /issues`` lists to ``token`` with ``query``, ``limit`` a page, and the totals the pages
    report."""
    seen: list[tuple[str, str]] = []
    totals: set[int] = set()
    offset = 0
    while True:
        response = await call(app, "GET", f"/api/v1/issues?limit={limit}&offset={offset}{query}", token)
        assert response.status_code == 200, response.text
        body = response.json()
        totals.add(body["total"])
        if not body["issues"]:
            return seen, totals
        seen += [(i["id"], i["repo"]) for i in body["issues"]]
        offset += limit


#: Each query the list takes, every filter and order among them.
QUERIES = [
    "",
    "&status=open",
    "&priority=high",
    f"&search={TERM}",
    f"&repo={UNCOVERED}",
    f"&repo={COVERED}",
    "&parent_issue_id=any",
    "&sort=updated_at&order=asc",
    "&sort=priority",
]


@pytest.mark.parametrize("query", QUERIES)
async def test_the_issue_list_pages_and_counts_only_the_key_s_issues(
    query: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        await _more_issues(db)
        everyone, everyone_totals = await _pages(app, keys.open, query, 200)
        limited, limited_totals = await _pages(app, keys.limited, query, 1)
    own = [item for item in everyone if item[1] == COVERED]
    assert any(repo == UNCOVERED for _id, repo in everyone) or query == f"&repo={COVERED}", (
        f"{query}: a key without a list is shown the other repository's issues, so the check measures something"
    )
    assert limited == own, f"{query}: the limited key's pages hold its own issues, each once, in order: {limited}"
    assert limited_totals == {len(own)}, f"{query}: every page's total counts the key's issues alone: {limited_totals}"
    assert everyone_totals == {len(everyone)}, everyone_totals


async def test_another_repository_s_issues_list_as_a_repository_s_with_none(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        other = await call(app, "GET", f"/api/v1/issues?repo={UNCOVERED}", keys.limited)
        nobody = await call(app, "GET", "/api/v1/issues?repo=nobody/nobody", keys.limited)
        everyone = await call(app, "GET", f"/api/v1/issues?repo={UNCOVERED}", keys.open)
    assert (other.status_code, other.content) == (nobody.status_code, nobody.content), (
        f"another repository's issues list as a repository with none: {other.text} against {nobody.text}"
    )
    assert everyone.json()["total"] > 0, everyone.text


def _detail(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return {
        "children": [c["id"] for c in body["children"]],
        "jobs": [j["job_id"] for j in body["jobs"]],
        "children_count": body["children_count"],
        "jobs_count": body["jobs_count"],
    }


async def _row(app: Any, token: str, issue_id: str) -> dict[str, Any]:
    """The counts ``GET /issues`` lists for ``issue_id``."""
    body = (await call(app, "GET", "/api/v1/issues?limit=200&parent_issue_id=any", token)).json()
    (row,) = [i for i in body["issues"] if i["id"] == issue_id]
    return {"children_count": row["children_count"], "jobs_count": row["jobs_count"]}


async def test_an_issue_shows_a_key_only_the_children_jobs_and_questions_of_its_repositories(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIXED is the key's, but its child, its job and that job's question are the other repository's: the key reads
    none of them, and the issue's counts count none of them, in the issue and in the list."""
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        limited = _detail(await call(app, "GET", f"/api/v1/issues/{MIXED}", keys.limited))
        everyone = _detail(await call(app, "GET", f"/api/v1/issues/{MIXED}", keys.open))
        own = _detail(await call(app, "GET", f"/api/v1/issues/{ISSUE[COVERED]}", keys.limited))
        limited_row, everyone_row = await _row(app, keys.limited, MIXED), await _row(app, keys.open, MIXED)
        questions = {
            name: [q["id"] for q in (await call(app, "GET", f"/api/v1/issues/{MIXED}/inputs", token)).json()]
            for name, token in (("limited", keys.limited), ("open", keys.open))
        }
        own_questions = (await call(app, "GET", f"/api/v1/issues/{ISSUE[COVERED]}/inputs", keys.limited)).json()
    assert limited == {"children": [], "jobs": [], "children_count": 0, "jobs_count": 0}, (
        f"the limited key reads no child or job of {UNCOVERED} under its issue, nor counts one: {limited}"
    )
    assert everyone == {"children": [CHILD_ELSEWHERE], "jobs": [JOB_ELSEWHERE], "children_count": 1, "jobs_count": 1}
    assert limited_row == {"children_count": 0, "jobs_count": 0} and everyone_row == {
        "children_count": 1,
        "jobs_count": 1,
    }, f"the list counts the same: {limited_row} against {everyone_row}"
    assert questions == {"limited": [], "open": [INPUT_ELSEWHERE]}, (
        f"the limited key reads no question a job of {UNCOVERED} asked about its issue: {questions}"
    )
    assert own == {"children": [CHILD[COVERED]], "jobs": [JOB[COVERED]], "children_count": 1, "jobs_count": 1}, own
    assert [q["id"] for q in own_questions] == [INPUT[COVERED]], (
        f"the key reads its own issue's questions: {own_questions}"
    )


async def test_an_issue_names_no_parent_the_key_does_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CHILD_HERE is the key's, under a parent of the other repository: it names no parent to the key, in the issue
    and in the list; the key's own child names its own parent."""
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        shown = {}
        for name, token in (("limited", keys.limited), ("open", keys.open)):
            listed = (await call(app, "GET", "/api/v1/issues?parent_issue_id=any&limit=200", token)).json()["issues"]
            shown[name] = {
                "issue": (await call(app, "GET", f"/api/v1/issues/{CHILD_HERE}", token)).json()["parent_issue_id"],
                "listed": {i["id"]: i["parent_issue_id"] for i in listed if i["id"] in (CHILD_HERE, CHILD[COVERED])},
            }
    assert shown["limited"] == {
        "issue": None,
        "listed": {CHILD_HERE: None, CHILD[COVERED]: ISSUE[COVERED]},
    }, f"the key's issue names no parent of {UNCOVERED}: {shown['limited']}"
    assert shown["open"] == {
        "issue": MIXED_HIDDEN,
        "listed": {CHILD_HERE: MIXED_HIDDEN, CHILD[COVERED]: ISSUE[COVERED]},
    }


async def test_an_issue_names_no_agent_running_on_another_repository_s_job(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIXED's job elsewhere is running: the issue's ``active_agent`` names its agent to a key without a list only."""
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        await db.execute("UPDATE jobs SET status = 'running' WHERE job_id = $1", JOB_ELSEWHERE)
        await db.execute(
            "INSERT INTO traces (trace_id, job_id, agent_type, model, result) VALUES ('tr-running', $1, 'reviewer', "
            "'m', 'success')",
            JOB_ELSEWHERE,
        )
        shown = {
            name: (
                (await call(app, "GET", f"/api/v1/issues/{MIXED}", token)).json()["active_agent"],
                [
                    i["active_agent"]
                    for i in (await call(app, "GET", "/api/v1/issues", token)).json()["issues"]
                    if i["id"] == MIXED
                ],
            )
            for name, token in (("limited", keys.limited), ("open", keys.open))
        }
    assert shown == {"limited": (None, [None]), "open": ("reviewer", ["reviewer"])}, (
        f"the key's issue names no agent running on {UNCOVERED}'s job: {shown}"
    )


async def test_a_job_names_no_issue_and_lists_no_question_the_key_does_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JOB_HERE is the key's, but its issue, and so its question, are the other repository's."""
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        shown: dict[str, Any] = {}
        for name, token in (("limited", keys.limited), ("open", keys.open)):
            listed = (await call(app, "GET", "/api/v1/jobs?limit=200", token)).json()["jobs"]
            shown[name] = {
                "job": (await call(app, "GET", f"/api/v1/jobs/{JOB_HERE}", token)).json()["issue_id"],
                "listed": {j["job_id"]: j["issue_id"] for j in listed if j["job_id"] in (JOB_HERE, JOB[COVERED])},
                "questions": [
                    q["id"] for q in (await call(app, "GET", f"/api/v1/jobs/{JOB_HERE}/inputs", token)).json()
                ],
            }
        own = (await call(app, "GET", f"/api/v1/jobs/{JOB[COVERED]}", keys.limited)).json()["issue_id"]
        own_questions = (await call(app, "GET", f"/api/v1/jobs/{JOB[COVERED]}/inputs", keys.limited)).json()
    assert shown["limited"] == {
        "job": None,
        "listed": {JOB_HERE: None, JOB[COVERED]: ISSUE[COVERED]},
        "questions": [],
    }, f"the limited key's job names no issue of {UNCOVERED}, and lists no question about one: {shown['limited']}"
    assert shown["open"] == {
        "job": MIXED_HIDDEN,
        "listed": {JOB_HERE: MIXED_HIDDEN, JOB[COVERED]: ISSUE[COVERED]},
        "questions": [INPUT_HERE],
    }, shown["open"]
    assert own == ISSUE[COVERED], f"the key's own job names its own issue: {own}"
    assert [q["id"] for q in own_questions] == [INPUT[COVERED]], own_questions


#: An issue with no repository, as a tracker may file one before its repository is known, and an id as long that names
#: nothing.
NO_REPOSITORY, NO_REPOSITORY_MISSING = "iss-norepo", "iss-zzzzzz"
assert len(NO_REPOSITORY) == len(NO_REPOSITORY_MISSING)


async def test_an_issue_with_no_repository_is_no_restricted_key_s(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        await db.execute(
            "INSERT INTO issues (id, title, repo, status) VALUES ($1, 'No repository', NULL, 'open')", NO_REPOSITORY
        )
        reads = {
            path: (
                await call(app, "GET", path.format(NO_REPOSITORY), keys.limited),
                await call(app, "GET", path.format(NO_REPOSITORY_MISSING), keys.limited),
                await call(app, "GET", path.format(NO_REPOSITORY), keys.open),
            )
            for path in ("/api/v1/issues/{}", "/api/v1/issues/{}/activity", "/api/v1/issues/{}/inputs")
        }
        listed = (await call(app, "GET", "/api/v1/issues?limit=200&parent_issue_id=any", keys.limited)).json()
        before = await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'")
        changed = await call(app, "PUT", f"/api/v1/issues/{NO_REPOSITORY}", keys.limited, {"title": "Renamed"})
        refusal = await db.fetchrow(
            "SELECT actor, details->>'repo' AS repo FROM audit_log WHERE action = 'apikey.repo_refused' "
            "ORDER BY id DESC LIMIT 1"
        )
        after = await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'")
        title = await db.fetchval("SELECT title FROM issues WHERE id = $1", NO_REPOSITORY)
    for path, (hidden, missing, everyone) in reads.items():
        assert (hidden.status_code, hidden.content) == (missing.status_code, missing.content), (
            f"{path}: an issue with no repository reads as a missing one: {hidden.text} against {missing.text}"
        )
        assert everyone.status_code == 200, f"{path}: a key without a list reads it: {everyone.text}"
    assert NO_REPOSITORY not in str(listed), f"the limited key's list leaves it out: {listed}"
    assert changed.status_code == 404 and changed.json() == {"detail": "Issue not found"}, changed.text
    assert after == before + 1 and dict(refusal or {}) == {"actor": REPO_ALLOWLIST, "repo": "(no repository)"}, refusal
    assert title == "No repository", "the refused call changed nothing"


async def _more_approvals(db: DatabasePool) -> None:
    """Newer approvals in the uncovered repository, of every status the list filters by, and a second pending one in
    the covered repository, older than them."""
    for i in range(NEWER):
        await db.execute(
            "INSERT INTO merge_approvals (id, repo, pr_number, pr_url, head_sha, status, created_at) "
            "VALUES ($1, $2, $3, $4, 'abc', $5, $6)",
            f"ma-newer-{i:03d}",
            UNCOVERED,
            100 + i,
            f"https://github.com/{UNCOVERED}/pull/{100 + i}",
            ("pending", "approved", "rejected")[i % 3],
            NOW + timedelta(seconds=i),
        )
    await db.execute(
        "INSERT INTO merge_approvals (id, repo, pr_number, pr_url, head_sha, status, created_at) "
        "VALUES ('ma-older-widget', $1, 8, $2, 'abc', 'approved', $3)",
        COVERED,
        f"https://github.com/{COVERED}/pull/8",
        NOW - timedelta(days=1),
    )


@pytest.mark.parametrize("status", [None, "pending", "approved"])
async def test_the_merge_approvals_list_holds_the_key_s_own_at_every_limit(
    status: str | None, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    query = f"&status={status}" if status else ""
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        await _more_approvals(db)
        everyone = (await call(app, "GET", f"/api/v1/merge-approvals?limit=200{query}", keys.open)).json()["items"]
        limits: dict[int, list[str]] = {}
        for limit in (1, 2, 50, 200):
            page = await call(app, "GET", f"/api/v1/merge-approvals?limit={limit}{query}", keys.limited)
            limits[limit] = [a["id"] for a in page.json()["items"]]
    own = [a["id"] for a in everyone if a["repo"] == COVERED]
    assert everyone[0]["repo"] == UNCOVERED and len(everyone) > len(own) + 2, "newer approvals elsewhere fill a page"
    assert own, f"the key has approvals of its own with {query or 'no filter'}: {everyone}"
    for limit, ids in limits.items():
        assert ids == own[:limit], f"limit {limit}: the limited key's approvals are its own, newest first: {ids}"
