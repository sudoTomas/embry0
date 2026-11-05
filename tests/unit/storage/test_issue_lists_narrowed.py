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
    assert sorted(r["id"] for r in narrowed) == ["iss-old", "iss-web"] and total == 2, (
        f"narrowed to acme/web, the list holds its issue and the one under its old slug, and no other: {narrowed}"
    )
    counts = {r["id"]: (r["children_count"], r["children_closed_count"], r["jobs_count"]) for r in narrowed}
    assert counts == {"iss-old": (1, 1, 1), "iss-web": (1, 1, 1)}, f"each counts only acme/web's: {counts}"
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
