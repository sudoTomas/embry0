"""The case queries narrowed in SQL to the repositories a key covers, on a real database.

``HealingCasesRepository.list_page``, ``get`` and ``get_detail`` take the slugs
a key covers (``covered_slugs``, lower-cased, each with its old slugs from
``REPO_RENAMES``) and keep only the cases of those repositories; a case stored
under a repository's old slug, or in another case, is selected through the
key's entry; another repository's case, one filed on an owner and one filed on
nothing are not. A case's jobs and pull requests, and its row's counts of them
and its current pull request, are only those of the covered repositories: a
job's repository is ``job_repository_sql``'s, a pull request's the one its
address names (``pr_repository_sql``, which reads an address as
``pr_repository`` does). ``None`` keeps every row and an empty list none.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories import healing_cases
from embry0.storage.repositories.healing_cases import HealingCasesRepository

pytestmark = pytest.mark.requires_postgres

#: What covered_slugs gives a key covering acme/web, which was Acme/Old-Web.
COVERED = ["acme/old-web", "acme/web"]
#: Each case's repository: the key's, the same under its old slug in another case, another, an owner, and nothing.
CASES = {"hc-web": "acme/web", "hc-old": "Acme/Old-Web", "hc-api": "acme/api", "hc-own": "acme", "hc-none": ""}
#: A pull request in each repository, under each address form, and what its address names.
PULLS = {
    "https://github.com/acme/web/pull/1": "acme/web",
    "https://github.com/Acme/Old-Web/pull/2/files": "Acme/Old-Web",
    "https://github.com/acme/api/pull/3": "acme/api",
    "https://gitlab.example.com/acme/web/-/merge_requests/4": "acme/web",
    "https://gitlab.example.com/acme/sub/web/-/merge_requests/5": "acme/sub/web",
    "https://github.com/acme/pull/pull/6": "acme/pull",
    "https://github.com/acme/web/issues/7": None,
    "not a url": None,
}
NOW = datetime.now(UTC)


async def _seed(db: DatabasePool) -> None:
    """Each case with a job of acme/web (by its git context alone) and one of acme/api, and the pull requests of
    acme/web and of acme/api, the merged one acme/api's."""
    for n, (case_id, repo) in enumerate(CASES.items()):
        await db.execute(
            "INSERT INTO healing_cases (id, repo, title, origin, stage, created_at) VALUES ($1, $2, 't', 'watcher', "
            "'pr_open', $3)",
            case_id,
            repo,
            NOW - timedelta(minutes=n),
        )
        for job_repo in ("acme/web", "acme/api"):
            job = f"{case_id}-job-{job_repo.split('/')[1]}"
            await db.execute(
                "INSERT INTO jobs (job_id, repo, context, task, status) VALUES ($1, NULL, $2, 't', 'completed')",
                job,
                {"type": "git", "repo": job_repo},
            )
            await db.execute("INSERT INTO healing_case_jobs (case_id, job_id) VALUES ($1, $2)", case_id, job)
        for number, (repo, state) in enumerate((("acme/web", "open"), ("acme/api", "merged")), start=1):
            await db.execute(
                "INSERT INTO healing_case_prs (case_id, url, number, state) VALUES ($1, $2, $3, $4)",
                case_id,
                f"https://github.com/{repo}/pull/{n * 10 + number}",
                number,
                state,
            )


def _take_repos() -> None:
    """The queries take the repositories a key covers, and a case's repository is read back; a tree without them fails
    here with a message rather than a TypeError."""
    missing = [
        f"HealingCasesRepository.{name}"
        for name in ("list_page", "get", "get_detail")
        if "repos" not in inspect.signature(getattr(HealingCasesRepository, name)).parameters
    ]
    assert missing == [], f"queries that cannot be narrowed to a key's repositories (no repos=): {missing}"
    assert hasattr(HealingCasesRepository, "repositories_of"), "a case's repository cannot be read back"


async def test_the_case_list_holds_only_the_covered_repositories_cases(db_with_migrations: DatabasePool) -> None:
    _take_repos()
    await _seed(db_with_migrations)
    cases = HealingCasesRepository(db_with_migrations)
    narrowed, total = await cases.list_page(repos=COVERED, limit=200)
    every, every_total = await cases.list_page(limit=200)
    none, none_total = await cases.list_page(repos=[], limit=200)
    assert [r["id"] for r in narrowed] == ["hc-web", "hc-old"] and total == 2, (
        f"narrowed to acme/web, the list holds its case and the one under its old slug, and no other: {narrowed}"
    )
    rows = {r["id"]: (r["jobs_count"], r["prs_count"], r["pr_url"]) for r in narrowed}
    assert rows == {
        "hc-web": (1, 1, "https://github.com/acme/web/pull/1"),
        "hc-old": (1, 1, "https://github.com/acme/web/pull/11"),
    }, f"each row counts and picks only acme/web's jobs and pull requests: {rows}"
    assert every_total == len(CASES) and {(r["jobs_count"], r["prs_count"]) for r in every} == {(2, 2)}, every
    assert {r["pr_url"].split("/pull/")[0] for r in every} == {"https://github.com/acme/api"}, "unnarrowed, as before"
    assert none == [] and none_total == 0, none
    paged, paged_total = await cases.list_page(repos=COVERED, limit=1, after=(narrowed[0]["created_at"], "hc-web"))
    assert [r["id"] for r in paged] == ["hc-old"] and paged_total == 2, "a later page of the narrowed list"


async def test_one_case_is_read_within_the_covered_repositories(db_with_migrations: DatabasePool) -> None:
    _take_repos()
    await _seed(db_with_migrations)
    cases = HealingCasesRepository(db_with_migrations)
    read = {case_id: await cases.get(case_id, repos=COVERED) for case_id in CASES}
    assert {c for c, row in read.items() if row is not None} == {"hc-web", "hc-old"}, (
        f"another repository's case, an owner's and one filed on nothing are not found: {read}"
    )
    detail = await cases.get_detail("hc-old", repos=COVERED)
    assert detail is not None, "the case under its old slug is the key's"
    assert [j["job_id"] for j in detail["jobs"]] == ["hc-old-job-web"], detail["jobs"]
    assert [p["url"] for p in detail["prs"]] == ["https://github.com/acme/web/pull/11"], detail["prs"]
    assert await cases.get_detail("hc-api", repos=COVERED) is None
    assert await cases.get_detail("hc-web", repos=[]) is None
    whole = await cases.get_detail("hc-api")
    assert whole is not None and (len(whole["jobs"]), len(whole["prs"])) == (2, 2), "unnarrowed, as before"
    assert await cases.repositories_of(["hc-web", "hc-none", "hc-nosuch"]) == {"hc-web": "acme/web", "hc-none": None}


async def test_a_pull_request_s_repository_reads_the_same_in_sql_and_in_python(
    db_with_migrations: DatabasePool,
) -> None:
    assert hasattr(healing_cases, "pr_repository") and hasattr(healing_cases, "pr_repository_sql"), (
        "a pull request's repository cannot be read from its address: no pr_repository or pr_repository_sql"
    )
    for url, repo in PULLS.items():
        assert healing_cases.pr_repository(url) == repo, url
        sql = await db_with_migrations.fetchval(
            f"SELECT {healing_cases.pr_repository_sql('p')} FROM (SELECT $1::text AS url) p", url
        )
        assert sql == repo, f"{url}: SQL reads {sql!r}, Python {repo!r}"


async def test_a_merge_carried_by_a_pull_request_elsewhere_is_marked(db_with_migrations: DatabasePool) -> None:
    """``merge_elsewhere``: the case's merge is the merge of a pull request in none of the covered repositories. hc-web's
    merge is its acme/api pull request's, hc-old's its acme/web one's, then no pull request's (not attributed), then
    one whose address names no repository; read whole, no row is marked."""
    db = db_with_migrations
    _take_repos()
    await _seed(db)
    await db.execute("UPDATE healing_cases SET merge_sha = 'sha-' || id")
    await db.execute(
        "UPDATE healing_case_prs SET merge_sha = 'sha-' || case_id "
        "WHERE (case_id = 'hc-web' AND url LIKE '%/acme/api/%') OR (case_id = 'hc-old' AND url LIKE '%/acme/web/%')"
    )
    cases = HealingCasesRepository(db)
    listed, _total = await cases.list_page(repos=COVERED, limit=200)
    marked = {r["id"]: r.get("merge_elsewhere") for r in listed}
    one = {c: (await cases.get_detail(c, repos=COVERED) or {}).get("merge_elsewhere") for c in ("hc-web", "hc-old")}
    whole, _total = await cases.list_page(limit=200)
    assert marked == {"hc-web": True, "hc-old": False}, (
        f"the list marks the merge of a pull request elsewhere: {marked}"
    )
    assert one == {"hc-web": True, "hc-old": False}, f"so does one case: {one}"
    assert not any("merge_elsewhere" in r for r in whole), "read whole, no row is marked"
    assert "merge_elsewhere" not in (await cases.get_detail("hc-web") or {}), "nor one case read whole"
    await db.execute("UPDATE healing_cases SET merge_sha = 'sha-nobody' WHERE id = 'hc-old'")
    alone = (await cases.get_detail("hc-old", repos=COVERED) or {}).get("merge_elsewhere")
    assert alone is False, f"a merge no pull request carries is not attributed: {alone}"
    await db.execute(
        "INSERT INTO healing_case_prs (case_id, url, state, merge_sha) "
        "VALUES ('hc-old', 'https://github.com/pull/9', 'merged', 'sha-nobody')"
    )
    unnamed = (await cases.get_detail("hc-old", repos=COVERED) or {}).get("merge_elsewhere")
    listed_unnamed = {r["id"]: r.get("merge_elsewhere") for r in (await cases.list_page(repos=COVERED, limit=200))[0]}
    assert (unnamed, listed_unnamed["hc-old"]) == (True, True), (
        f"a merge a pull request whose address names no repository carries is no covered repository's: {unnamed}"
    )
