"""The totals' queries narrowed to the repositories a key covers, on a real database.

Each query a total of a key with an allowlist is read from takes the slugs the
key covers (``covered_slugs``, lower-cased, each with its old slugs from
``REPO_RENAMES``) and counts only their work in SQL: the agents' runs
(``GET /traces``), the characters' quest totals (``GET /world``), the
progression's journal (the quests and repo diaries), the background
features' model calls and the setup runs. A job belongs to the repository it
works on, its ``repo`` or else its git context's (``job_repository_sql``), so a
job of the key's repository stored under its old slug, in another case, or in
its context alone is counted; another repository's job, and a job of no
repository, are not. ``None`` counts everything as before and an empty list
nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.cache.prebaked_staleness import summarize_prebuilt_images
from embry0.services.repo_setup import cost_facts
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.background_spend import BackgroundSpendRepository
from embry0.storage.repositories.progression import ProgressionRepository
from embry0.storage.repositories.qa_image_tags import QAImageTagsRepository
from embry0.storage.repositories.traces import TracesRepository
from embry0.storage.repositories.world import WorldRepository

pytestmark = pytest.mark.requires_postgres

#: What covered_slugs gives a key covering acme/web, which was Acme/Old-Web.
COVERED = ["acme/old-web", "acme/web"]
NOW = datetime.now(UTC)
#: Each job: (its repo column, its git context's repository). The first three are the key's.
JOBS: dict[str, tuple[str | None, str | None]] = {
    "job-web": ("acme/web", None),
    "job-old": ("Acme/Old-Web", None),
    "job-context": (None, "ACME/web"),
    "job-api": ("acme/api", None),
    "job-nowhere": (None, None),
}
OWN = ("job-web", "job-old", "job-context")


async def _seed(db: DatabasePool) -> None:
    for n, (job, (repo, in_context)) in enumerate(JOBS.items()):
        context = {"type": "git", "repo": in_context} if in_context else {"type": "http", "url": "https://x.test"}
        at = NOW - timedelta(hours=1, minutes=n)
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, context, created_at, started_at, "
            "finished_at, total_cost_usd) VALUES ($1, $2, 't', 'completed', 'onboard', $3::jsonb, $4, $4, $4, $5)",
            job,
            repo,
            context,
            at,
            float(n + 1),
        )
        for agent in ("developer", "review"):
            await db.execute(
                "INSERT INTO traces (trace_id, job_id, agent_type, model, result, cost_usd, created_at) "
                "VALUES ($1, $2, $3, 'm', 'success', 1.0, $4)",
                f"trc-{job}-{agent}",
                job,
                agent,
                at,
            )
    for repo in ("acme/web", "Acme/Old-Web", "acme/api", None):
        await db.execute(
            "INSERT INTO background_spend (at, feature, model, repo, cost_usd) VALUES ($1, 'job_brief', 'm', $2, 1.0)",
            NOW - timedelta(minutes=1),
            repo,
        )
        if repo is not None:
            await db.execute(
                "INSERT INTO qa_image_tags (repo, image_tag, lockfile_sha, is_active) VALUES ($1, $2, 'sha', true)",
                repo,
                f"qa-{repo.lower().replace('/', '-')}:1",
            )


async def test_the_runs_of_the_key_s_jobs_are_listed_and_counted_before_the_page(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    await _seed(db)
    traces = TracesRepository(db)
    rows, total = await traces.list_all(repos=COVERED, limit=200)
    every, every_total = await traces.list_all(limit=200)
    none, none_total = await traces.list_all(repos=[], limit=200)
    page, page_total = await traces.list_all(agent_type="review", result="success", repos=COVERED, limit=1, offset=1)
    other, other_total = await traces.list_all(job_id="job-api", repos=COVERED)
    assert sorted(r["job_id"] for r in rows) == sorted(j for j in OWN for _ in range(2)) and total == 6, (
        f"the runs of the key's jobs, its old slug's and its context-only one's included, and no other: {total} {rows}"
    )
    assert every_total == 10 and len(every) == 10, f"unnarrowed, every run as before: {every_total}"
    assert (none, none_total) == ([], 0), "an empty list selects nothing"
    assert [r["trace_id"] for r in page] == ["trc-job-old-review"] and page_total == 3, (
        f"narrowed with the other filters, before the page: {page_total} {[r['trace_id'] for r in page]}"
    )
    assert (other, other_total) == ([], 0), "another repository's job has no runs for the key"


async def test_the_characters_quest_totals_count_the_key_s_jobs(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _seed(db)
    world = WorldRepository(db)
    narrowed = await world.stats(repos=COVERED)
    every = await world.stats()
    one = await world.stats("review", repos=COVERED)
    assert {agent: int(row["quests"]) for agent, row in narrowed.items()} == {"developer": 3, "review": 3}, narrowed
    assert {agent: int(row["quests"]) for agent, row in every.items()} == {"developer": 5, "review": 5}, every
    assert list(one) == ["review"] and int(one["review"]["quests"]) == 3, one
    assert await world.stats(repos=[]) == {}, "an empty list counts nothing"


async def test_the_journal_holds_the_key_s_jobs_and_their_runs(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _seed(db)
    progression = ProgressionRepository(db)
    narrowed = await progression.journal(repos=COVERED)
    of_agents = await progression.journal(["review"], repos=COVERED)
    every = await progression.journal()
    nothing = await progression.journal(["review"], repos=[])
    own = ["ACME/web", "Acme/Old-Web", "acme/web"]
    assert sorted(j.repo for j in narrowed.jobs) == own, f"the key's jobs, as each stores its repository: {narrowed}"
    assert sorted(r.repo for r in narrowed.runs) == sorted(own * 2), narrowed.runs
    assert sorted(r.repo for r in of_agents.runs) == own and {r.agent_type for r in of_agents.runs} == {"review"}, (
        f"the agents and the repositories narrow together: {of_agents.runs}"
    )
    assert sorted(j.repo for j in every.jobs) == sorted([*own, "acme/api"]), "unnarrowed, every job with a repository"
    assert (nothing.jobs, nothing.runs) == ([], []), "an empty list selects nothing"


async def test_the_background_calls_the_setup_runs_and_the_images_of_the_key_s_repositories(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    await _seed(db)
    spend = BackgroundSpendRepository(db)
    assert [row["calls"] for row in await spend.this_month(COVERED)] == [2], "the key's two calls"
    assert [row["calls"] for row in await spend.this_month()] == [4], "every call, one of no repository included"
    assert await spend.this_month([]) == []

    class Budget:
        async def settings(self) -> Any:
            return SimpleNamespace(max_budget_per_job_usd=5.0)

    class Provider:
        async def get(self) -> dict[str, Any]:
            return {"model_medium": "claude-test"}

    state = SimpleNamespace(db=db, budget_policy=Budget(), provider_repo=Provider())
    narrowed = await cost_facts(state, "acme/web", resolve_account=False, repos=COVERED)
    every = await cost_facts(state, "acme/web", resolve_account=False)
    assert (narrowed["recorded_runs"], narrowed["typical_cost_usd"]) == (3, 2.0), narrowed
    assert (every["recorded_runs"], every["typical_cost_usd"]) == (5, 3.0), every

    class Digests:
        async def current(self, image: str) -> str | None:
            return None

    images = QAImageTagsRepository(db)
    kept = await summarize_prebuilt_images(images, Digests(), keep=lambda repo: repo.lower() in COVERED)
    whole = await summarize_prebuilt_images(images, Digests())
    assert kept is not None and kept["active"] == 2, kept
    assert sorted(i["repo"] for i in kept["unknown"]) == ["Acme/Old-Web", "acme/web"], kept
    assert whole is not None and whole["active"] == 3, whole
