"""The issue queries narrowed in SQL to the repositories a key covers, on a real database.

``IssuesRepository.list_all``, ``get`` and ``get_children`` take the slugs a key
covers (``covered_slugs``, lower-cased, each with its old slugs from
``REPO_RENAMES``) and keep only the issues of those repositories, and count only
their children and jobs; ``IssueInputsRepository.list_by_issue`` and
``list_by_job`` keep only the questions whose job and issue are both of them.
A row stored under a repository's old slug, or in another case, is selected
through the key's entry; another repository's row, or one with no repository,
is not; ``None`` keeps every row and an empty list none.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.issue_inputs import IssueInputsRepository
from embry0.storage.repositories.issues import IssuesRepository

pytestmark = pytest.mark.requires_postgres

#: What covered_slugs gives a key covering acme/web, which was Acme/Old-Web.
COVERED = ["acme/old-web", "acme/web"]
#: Each parent issue's repository: the key's, the same under its old slug in another case, another, and none.
PARENTS = {"iss-web": "acme/web", "iss-old": "Acme/Old-Web", "iss-api": "acme/api", "iss-none": None}


async def _seed(db: DatabasePool) -> None:
    """Each parent with a child, a job and a question of each repository; the job of a git context alone stores its
    repository in the context."""
    for parent, repo in PARENTS.items():
        await db.execute("INSERT INTO issues (id, title, repo) VALUES ($1, 't', $2)", parent, repo)
    for parent in PARENTS:
        for n, repo in enumerate(("acme/web", "acme/api")):
            child, job = f"{parent}-kid{n}", f"{parent}-job{n}"
            await db.execute(
                "INSERT INTO issues (id, title, repo, parent_issue_id, status) VALUES ($1, 't', $2, $3, 'closed')",
                child,
                repo,
                parent,
            )
            await db.execute(
                "INSERT INTO jobs (job_id, repo, context, task, status, issue_id) VALUES ($1, NULL, $2, 't', "
                "'completed', $3)",
                job,
                {"type": "git", "repo": repo},
                parent,
            )
            await db.execute(
                "INSERT INTO issue_inputs (id, issue_id, job_id, question) VALUES ($1, $2, $3, 'q')",
                f"{job}-q",
                parent,
                job,
            )


def _take_repos() -> None:
    """The queries take the repositories a key covers, and an issue's repository is read back; a tree without them
    fails here with a message rather than a TypeError."""
    missing = [
        f"{cls.__name__}.{name}"
        for cls, names in (
            (IssuesRepository, ("list_all", "get", "get_children")),
            (IssueInputsRepository, ("list_by_issue", "list_by_job")),
        )
        for name in names
        if "repos" not in inspect.signature(getattr(cls, name)).parameters
    ]
    assert missing == [], f"queries that cannot be narrowed to a key's repositories (no repos=): {missing}"
    assert hasattr(IssuesRepository, "repositories_of"), "an issue's repository cannot be read back: no repositories_of"


async def test_the_issue_list_holds_only_the_covered_repositories_issues(db_with_migrations: DatabasePool) -> None:
    _take_repos()
    await _seed(db_with_migrations)
    issues = IssuesRepository(db_with_migrations)
    narrowed, total = await issues.list_all(repos=COVERED, top_level_only=True, limit=200)
    every, every_total = await issues.list_all(top_level_only=True, limit=200)
    none, none_total = await issues.list_all(repos=[], top_level_only=True, limit=200)
    # acme/web's children of a parent it does not read (acme/api's, and one of no repository) are at its top level,
    # as an issue whose parent was deleted is.
    top = ["iss-api-kid0", "iss-none-kid0", "iss-old", "iss-web"]
    assert sorted(r["id"] for r in narrowed) == top and total == len(top), (
        f"narrowed to acme/web, the list holds its issue, the one under its old slug and its children of a parent it "
        f"does not read, and no other: {narrowed}"
    )
    counts = {r["id"]: (r["children_count"], r["children_closed_count"], r["jobs_count"]) for r in narrowed}
    assert counts == {
        "iss-old": (1, 1, 1),
        "iss-web": (1, 1, 1),
        "iss-api-kid0": (0, 0, 0),
        "iss-none-kid0": (0, 0, 0),
    }, f"each counts only acme/web's: {counts}"
    assert every_total == len(PARENTS) and {r["jobs_count"] for r in every} == {2}, "unnarrowed, as before"
    assert none == [] and none_total == 0, none


async def test_one_issue_and_its_children_and_questions_are_read_within_the_covered_repositories(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    _take_repos()
    await _seed(db)
    issues, inputs = IssuesRepository(db), IssueInputsRepository(db)
    read = {parent: await issues.get(parent, repos=COVERED) for parent in PARENTS}
    assert {p for p, row in read.items() if row is not None} == {"iss-web", "iss-old"}, (
        f"another repository's issue, and one with none, are not found: {read}"
    )
    assert (read["iss-web"] or {}).get("jobs_count") == 1, read
    unnarrowed = await issues.get("iss-api")
    assert unnarrowed is not None and unnarrowed["jobs_count"] == 2, f"unnarrowed, every job counts: {unnarrowed}"
    assert [c["id"] for c in await issues.get_children("iss-api", repos=COVERED)] == ["iss-api-kid0"]
    assert len(await issues.get_children("iss-api")) == 2 and await issues.get_children("iss-web", repos=[]) == []
    assert [q["id"] for q in await inputs.list_by_issue("iss-web", repos=COVERED)] == ["iss-web-job0-q"], (
        "an issue's questions are those its covered jobs asked"
    )
    assert await inputs.list_by_issue("iss-api", repos=COVERED) == [], "no question about another repository's issue"
    assert [q["id"] for q in await inputs.list_by_job("iss-old-job0", repos=COVERED)] == ["iss-old-job0-q"]
    assert await inputs.list_by_job("iss-api-job0", repos=COVERED) == [], "a covered job's question about acme/api's"
    assert await inputs.list_by_job("iss-none-job0", repos=COVERED) == [], "or about an issue with no repository"
    assert len(await inputs.list_by_issue("iss-api")) == 2 and len(await inputs.list_by_job("iss-api-job1")) == 1
    assert await issues.repositories_of(["iss-web", "iss-none", "iss-nosuch"]) == {
        "iss-web": "acme/web",
        "iss-none": None,
    }


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Every node of an ``EXPLAIN (FORMAT JSON)`` plan, depth first."""
    out = [plan]
    for child in plan.get("Plans", []):
        out += _nodes(child)
    return out


def _parent_read(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The scans of the parent the top-level filter reads (``issues p``)."""
    return [n for n in nodes if n.get("Relation Name") == "issues" and n.get("Alias") == "p"]


#: The join types of an anti-join. Postgres 16 also hashes the outer side: a hash join's "Right Anti" (#574).
ANTI_JOINS = frozenset({"Anti", "Right Anti"})


def _shape(plan: dict[str, Any]) -> list[tuple[str, str | None, str | None]]:
    """Each node's type, join type and relationship to its parent: what an assertion about a plan prints."""
    return [(n["Node Type"], n.get("Join Type"), n.get("Parent Relationship")) for n in _nodes(plan)]


def _parent_joined(plan: dict[str, Any]) -> bool:
    """Whether ``plan`` reads the parent in an anti-join of any form (hash, merge or nested loop; ``Anti`` or ``Right
    Anti``) and in no subplan, which a scan runs for its rows one at a time, or hashed, again on every execution."""
    nodes = _nodes(plan)
    anti = [n for n in nodes if n.get("Join Type") in ANTI_JOINS and _parent_read(_nodes(n))]
    subplan = [n for n in nodes if n.get("Parent Relationship") == "SubPlan" and _parent_read(_nodes(n))]
    return bool(anti) and not subplan


async def _queries(db: DatabasePool, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...]]]:
    """The count and the page ``list_all`` sends for a key covering :data:`COVERED`, with their arguments."""
    sent: list[tuple[str, tuple[Any, ...]]] = []
    for name in ("fetch", "fetchval"):
        method = getattr(db, name)

        async def recorded(sql: str, *args: Any, _method: Any = method) -> Any:
            sent.append((sql, args))
            return await _method(sql, *args)

        monkeypatch.setattr(db, name, recorded)
    await IssuesRepository(db).list_all(repos=COVERED, top_level_only=True, limit=50)
    monkeypatch.undo()
    assert len(sent) == 2, f"the list sends its count and its page: {[sql[:60] for sql, _ in sent]}"
    for sql, _args in sent:
        assert " issues p " in sql, f"the query reads no parent: it measures nothing: {sql[:200]}"
    return sent


async def _plan(conn: Any, sql: str, args: tuple[Any, ...]) -> dict[str, Any]:
    (explained,) = json.loads(await conn.fetchval("EXPLAIN (FORMAT JSON) " + sql, *args))
    plan: dict[str, Any] = explained["Plan"]
    return plan


async def test_a_key_s_top_level_is_an_anti_join_not_a_check_per_row(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The top level of a key's list reads each issue's parent once, joined: Postgres plans an anti-join for it, in the
    count and in the page, rather than a subplan it runs for every row (on a hundred thousand issues, a page of the
    dashboard's took half a second that way). An anti-join is the planner's shape for a bare ``NOT EXISTS`` whatever
    the table holds, so a small table pins it; ``parent_issue_id IS NULL OR`` before it is enough to lose it. Which
    side of a hash join the planner hashes follows the table's statistics, so either is an anti-join here."""
    db = db_with_migrations
    await _seed(db)
    for sql, args in await _queries(db, monkeypatch):
        plan = await _plan(db, sql, args)
        assert _parent_read(_nodes(plan)), f"the query reads no parent: it measures nothing: {sql[:200]}"
        assert _parent_joined(plan), f"the parent is read in a subplan, not an anti-join: {_shape(plan)}"


#: The planner settings that leave Postgres one way to join, and the rows beyond the seed that make it pick the side
#: of a hash join: (acme/web parents, children of them). A hash join hashes the parents (Anti) when a few parents have
#: many children, the children's column then a poor key to hash; with few children it hashes the issues it lists
#: (Right Anti). Each pair sits well inside its side: 3 to 300 parents gave Right Anti up to 300 children and Anti from
#: 3,000.
FORCED: dict[tuple[str, str], tuple[dict[str, str], tuple[int, int]]] = {
    ("Nested Loop", "Anti"): ({"enable_hashjoin": "off", "enable_mergejoin": "off"}, (30, 30)),
    ("Merge Join", "Anti"): ({"enable_hashjoin": "off", "enable_nestloop": "off"}, (30, 30)),
    ("Hash Join", "Anti"): ({"enable_nestloop": "off", "enable_mergejoin": "off"}, (30, 3000)),
    ("Hash Join", "Right Anti"): ({"enable_nestloop": "off", "enable_mergejoin": "off"}, (30, 30)),
}

#: The clause of a key's top level, and what it was before #560 made it a bare ``NOT EXISTS``: a subplan.
_TOP_LEVEL = re.compile(r"NOT EXISTS \(SELECT 1 FROM issues p WHERE .*?::text\[\]\)\)", re.DOTALL)


@pytest.mark.parametrize("shape", list(FORCED), ids=[" ".join(shape) for shape in FORCED])
async def test_every_anti_join_postgres_can_plan_is_accepted_and_a_subplan_never(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, shape: tuple[str, str]
) -> None:
    """Each form of anti-join, forced (the other joins off, and rows that make a hash join hash the outer side, the
    Right Anti join Postgres 16 plans), is one the plan test accepts, in the count and in the page; and the query with
    ``parent_issue_id IS NULL OR`` before the ``NOT EXISTS``, under the same settings, is refused: a subplan, hashed or
    run per row, which no setting turns into a join."""
    db = db_with_migrations
    await _seed(db)
    settings, (parents, children) = FORCED[shape]
    await db.execute(
        "INSERT INTO issues (id, title, repo) SELECT 'iss-more-' || g, 't', 'acme/web' FROM generate_series(1, $1) g",
        parents,
    )
    await db.execute(
        "INSERT INTO issues (id, title, repo, parent_issue_id) "
        "SELECT 'iss-more-kid-' || g, 't', 'acme/web', 'iss-more-' || (1 + g % $2) FROM generate_series(1, $1) g",
        children,
        parents,
    )
    await db.execute("ANALYZE issues")
    queries = await _queries(db, monkeypatch)
    async with db.transaction() as conn:
        for name, value in settings.items():
            await conn.execute(f"SET LOCAL {name} = {value}")
        (count_sql, count_args), _page = queries
        count = await _plan(conn, count_sql, count_args)
        joins = [(n["Node Type"], n.get("Join Type")) for n in _nodes(count) if _parent_read(_nodes(n))]
        assert shape in joins, f"the settings did not force {shape}, so this case measures nothing: {_shape(count)}"
        for sql, args in queries:
            plan = await _plan(conn, sql, args)
            assert _parent_joined(plan), f"{shape}: an anti-join is refused: {_shape(plan)}"
            with_or, found = _TOP_LEVEL.subn(lambda m: f"(i.parent_issue_id IS NULL OR {m.group(0)})", sql)
            assert found == 1, f"the top level's clause is not where it was: {sql[:300]}"
            refused = await _plan(conn, with_or, args)
            assert not _parent_joined(refused), f"{shape}: a subplan is accepted: {_shape(refused)}"


def _join(node_type: str, join_type: str) -> dict[str, Any]:
    scan = {"Node Type": "Seq Scan", "Relation Name": "issues", "Alias": "p", "Parent Relationship": "Inner"}
    other = {"Node Type": "Seq Scan", "Relation Name": "issues", "Alias": "i", "Parent Relationship": "Outer"}
    if node_type == "Hash Join":
        scan = {"Node Type": "Hash", "Parent Relationship": "Inner", "Plans": [scan]}
    return {
        "Node Type": "Aggregate",
        "Plans": [{"Node Type": node_type, "Join Type": join_type, "Plans": [other, scan]}],
    }


def _subplan(filter_text: str) -> dict[str, Any]:
    scan = {"Node Type": "Seq Scan", "Relation Name": "issues", "Alias": "p", "Parent Relationship": "SubPlan"}
    outer = {"Node Type": "Seq Scan", "Relation Name": "issues", "Alias": "i", "Filter": filter_text, "Plans": [scan]}
    return {"Node Type": "Aggregate", "Plans": [outer]}


@pytest.mark.parametrize(
    ("plan", "accepted"),
    [
        *(
            (_join(node, join), True)
            for node in ("Hash Join", "Merge Join", "Nested Loop")
            for join in ("Anti", "Right Anti")
        ),
        (_join("Hash Join", "Inner"), False),
        (_subplan("(NOT (SubPlan 1))"), False),
        (_subplan("(NOT (hashed SubPlan 2))"), False),
    ],
    ids=[
        *(f"{node} {join}" for node in ("Hash Join", "Merge Join", "Nested Loop") for join in ("Anti", "Right Anti")),
        "an inner join",
        "a subplan per row",
        "a hashed subplan",
    ],
)
def test_the_plan_check_accepts_any_anti_join_and_no_subplan(plan: dict[str, Any], accepted: bool) -> None:
    assert _parent_joined(plan) is accepted, _shape(plan)
