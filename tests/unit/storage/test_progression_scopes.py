"""The progression of a set of repositories (migration 149): a deployment holding only them, folded once.

A key with a repository allowlist reads the experience, levels, achievements,
quests and diaries of its repositories' work alone. On random histories of two
repositories written into Postgres:

- the service reading one repository's scope through its own rollup answers,
  field for field and at every step as the clock moves, what folding every raw
  row of that repository answers;
- either answers what the deployment's progression answers once every row
  that is not that repository's work is deleted: a deployment holding only it;
- each scope keeps a rollup of its own beside the deployment's, of the active
  pack alone, and a set no read advanced in two weeks is deleted, and each is
  cached apart.

And on rows of every table progression reads, with work that crosses from one
repository into the other: every fact fragment, every event and the journal of
a scope are what a deployment holding only that repository's rows reads.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.services.progression import ProgressionService
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.progression import ProgressionRepository
from embry0.storage.repositories.progression_rollup import ProgressionRollupRepository, RollupHead
from tests.unit.services.test_progression_rollup import Packs, differences
from tests.unit.storage.test_progression_rollup_repo import RULES, Writer

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

#: The repository the scope covers, as covered_slugs gives it; the Writer's other one is acme/lib.
SCOPE = ["acme/widgets"]


def _same(actual: object, expected: object, context: str) -> None:
    problems = differences(actual, expected)  # type: ignore[arg-type]
    assert not problems, f"{context}:\n" + "\n".join(problems[:12])


async def _history(db: DatabasePool, seed: int, clock: datetime) -> Writer:
    writer = Writer(db, random.Random(seed))
    await writer.setup()
    for _ in range(5):
        await writer.some(clock - timedelta(days=14), clock)
    return writer


@pytest.mark.parametrize("seed", range(3))
async def test_a_scope_s_rollup_answers_what_its_raw_rows_answer(db_with_migrations: DatabasePool, seed: int) -> None:
    db = db_with_migrations
    clock = [datetime(2025, 12, 20, 9, tzinfo=UTC)]
    writer = await _history(db, seed, clock[0])
    packs = Packs(RULES)
    service = ProgressionService(
        ProgressionRepository(db),
        packs=packs,
        rollups=ProgressionRollupRepository(db),
        ttl_seconds=0,
        now=lambda: clock[0],
    )
    reference = ProgressionService(ProgressionRepository(db), packs=packs, rollups=None, ttl_seconds=0)
    used = 0
    rng = random.Random(seed)
    for step in range(8):
        context = f"seed {seed}, step {step}"
        answer = await service.read(SCOPE)
        _same(answer, await reference.compute(packs.rules, SCOPE), context)
        await service.settle()
        answer = await service.read(SCOPE)
        used += answer.rollup is not None
        _same(answer, await reference.compute(packs.rules, SCOPE), context + ", after the write")
        clock[0] += timedelta(hours=rng.uniform(5, 40))
        await writer.some(clock[0] - timedelta(hours=40), clock[0])
    assert used >= 6, f"the scope's rollup answered only {used} of 8 steps"
    from embry0.storage.repositories.progression_rollup import scope_key

    scopes = [r["scope"] for r in await db.fetch("SELECT scope FROM progression_scope_rollups")]
    assert scopes == [scope_key(SCOPE)], f"the scope keeps a rollup of its own, the only one read: {scopes}"


@pytest.mark.parametrize("seed", range(3))
async def test_a_scope_reads_as_a_deployment_holding_only_its_repositories(
    db_with_migrations: DatabasePool, seed: int
) -> None:
    db = db_with_migrations
    clock = datetime(2025, 12, 20, 9, tzinfo=UTC)
    await _history(db, seed, clock)
    packs = Packs(RULES)
    service = ProgressionService(
        ProgressionRepository(db),
        packs=packs,
        rollups=ProgressionRollupRepository(db),
        ttl_seconds=0,
        now=lambda: clock,
    )
    await service.read(SCOPE)
    await service.settle()
    scoped = await service.read(SCOPE)
    assert scoped.rollup is not None, "read through the scope's rollup"
    everyone = await service.read()
    assert differences(scoped, everyone), "the other repository's work counts for the deployment: the test measures"
    # Every row that is not the repository's work, gone: what a deployment holding only it has.
    others = "lower(COALESCE(repo, context->>'repo')) IS DISTINCT FROM 'acme/widgets'"
    await db.execute(f"DELETE FROM traces WHERE job_id IN (SELECT job_id FROM jobs WHERE {others})")
    await db.execute(f"DELETE FROM jobs WHERE {others}")
    await db.execute(
        "DELETE FROM audit_log WHERE action = 'issue.input_created' "
        "AND NOT EXISTS (SELECT 1 FROM issue_inputs q WHERE q.id = details->>'input_id')"
    )
    alone = await ProgressionService(ProgressionRepository(db), packs=packs, rollups=None, ttl_seconds=0).read()
    _same(scoped, alone, f"seed {seed}: the scope against a deployment holding only acme/widgets")


async def test_each_scope_is_cached_apart(db_with_migrations: DatabasePool) -> None:
    """One service, a minute's cache: the deployment, each repository and both read in turn, each its own answer,
    and the deployment's read again is the one it cached."""
    db = db_with_migrations
    clock = datetime(2025, 12, 20, 9, tzinfo=UTC)
    await _history(db, 0, clock)
    packs = Packs(RULES)
    service = ProgressionService(
        ProgressionRepository(db), packs=packs, rollups=None, ttl_seconds=60, now=lambda: clock
    )
    reference = ProgressionService(ProgressionRepository(db), packs=packs, rollups=None, ttl_seconds=0)
    everyone = await service.read()
    for repos in (SCOPE, ["acme/lib"], ["acme/lib", "acme/widgets"], None):
        _same(await service.read(repos), await reference.compute(packs.rules, repos), f"read for {repos}")
    assert differences(await service.read(SCOPE), everyone), "a scope's answer is not the deployment's"
    assert await service.read() is everyone, "the deployment's answer is cached"


async def test_each_scope_keeps_its_rollup_and_a_set_nobody_reads_is_let_go(
    db_with_migrations: DatabasePool,
) -> None:
    from embry0.services.progression_rollup import Scope
    from embry0.storage.repositories.progression_rollup import DEPLOYMENT, UNREAD_FOR, scope_key

    db = db_with_migrations
    store = ProgressionRollupRepository(db)
    now = datetime(2026, 1, 10, tzinfo=UTC)

    def head(scope: Any, pack: str, built: datetime) -> RollupHead:
        return RollupHead(
            pack_hash=pack,
            fold_version="v",
            timezone="UTC",
            through_day=built.date(),
            boundary=built,
            state={},
            rewards=[],
            built_at=built,
            rebuilt_at=built,
            scope=scope.key,
            repos=scope.repos,
        )

    one, two, stale = Scope.of(["acme/widgets"]), Scope.of(["acme/lib", "acme/widgets"]), Scope.of(["acme/old"])
    async with store.writing() as w:
        await w.replace(head(Scope(), "p", now), [], [])
        await w.replace(head(stale, "p", now - UNREAD_FOR - timedelta(hours=1)), [], [])
        await w.replace(head(one, "p", now), [], [])
    async with store.writing() as w:
        await w.replace(head(two, "p", now), [], [])
    kept = {
        (r["scope"], r["pack_hash"]) for r in await db.fetch("SELECT scope, pack_hash FROM progression_scope_rollups")
    }
    assert kept == {(DEPLOYMENT, "p"), (one.key, "p"), (two.key, "p")}, (
        f"a full build keeps every scope of the pack read lately, and lets go of one nobody read: {kept}"
    )
    loaded = await store.load("p", one.key)
    assert loaded is not None and loaded.head.repos == ("acme/widgets",) and loaded.head.scope == one.key
    assert await store.load("p", two.key) is not None and await store.load("p") is not None
    async with store.writing() as w:
        await w.replace(head(one, "q", now), [], [])
    kept = {
        (r["scope"], r["pack_hash"]) for r in await db.fetch("SELECT scope, pack_hash FROM progression_scope_rollups")
    }
    assert kept == {(one.key, "q")}, (
        f"a build under another pack lets go of every scope's rollup of the old one: {kept}"
    )
    assert scope_key(["acme/widgets", "acme/lib"]) == scope_key(["acme/lib", "acme/widgets", "acme/lib"])
    assert scope_key(None) == DEPLOYMENT != scope_key([])


async def test_every_table_progression_reads_is_narrowed_in_a_scope() -> None:
    """A scope reads each table through an item of scope_ctes named for it: a table an event branch, a fact fragment
    or the journal reads that has none would be read whole for a key with an allowlist. Found from the SQL itself, so a
    new fragment reading a new table fails here until the scope narrows it."""
    import re

    from embry0.storage.repositories import progression as module

    texts = [module.events_sql(), module._FACTS_CTES, module._JOBS_SQL, module._RUNS_SQL, *module.FRAGMENTS.values()]  # noqa: SLF001
    # A table's name after FROM or JOIN; not a column (extract(... FROM m.at)), nor a subquery.
    read = {name for text in texts for name in re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)\b(?!\.)", text)}
    scoped = set(re.findall(r"^\s*([a-z_]+) AS NOT MATERIALIZED \(", module.scope_ctes(1), re.MULTILINE))
    assert read, "the walk over the SQL found no table: it measures nothing"
    assert read - scoped - {"merged", "healed"} == set(), (
        f"tables progression reads that a scope does not narrow: {sorted(read - scoped - {'merged', 'healed'})}"
    )
    assert scoped - read == set(), f"scope items no progression read reads: {sorted(scoped - read)}"


@dataclass(frozen=True)
class _Fragment:
    key: str
    fragment: str
    params: dict[str, Any]


#: A value for every parameter a fact fragment takes, wide enough that each fragment selects what it can.
_PARAMS: dict[str, Any] = {
    "max_usd": 1000.0,
    "hours": 10000.0,
    "count": 1,
    "review_agents": ["review", "pr-reviewer"],
    "qa_agents": ["qa"],
    "severity": None,
    "min_seen": 1,
    "min_score": 0,
    "agents": ["review", "developer", "triage"],
    "min_resumes": 0,
    "min_attempts": 1,
    "weekdays": [1, 2, 3, 4, 5, 6, 7],
    "from_hour": 0,
    "to_hour": 24,
    "dates": [f"{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 32)],
}

#: Where the wide value selects nothing: "ran longer than".
_OVER: dict[str, dict[str, Any]] = {"merged_running_over": {"hours": 1.0}}

#: What the other repository's rows carry, so a fact that shows any of them is found.
_THEIRS = "lib-only-text"


async def _every_kind(db: DatabasePool) -> None:
    """Rows of every table progression reads, in each repository, and work that crosses: a job of acme/widgets merged
    on an issue that is acme/lib's, and a question its job asked about that issue."""
    at = datetime(2025, 12, 10, 12, tzinfo=UTC)
    for repo, tag, title in (("acme/widgets", "w", "widgets-text"), ("acme/lib", "l", _THEIRS)):
        await db.execute(
            "INSERT INTO issues (id, title, repo) VALUES ($1, $2, $3), ($4, $2, $3)",
            f"iss-{tag}-parent",
            title,
            repo,
            f"iss-{tag}-child",
        )
        await db.execute(
            "UPDATE issues SET parent_issue_id = $1 WHERE id = $2", f"iss-{tag}-parent", f"iss-{tag}-child"
        )
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, issue_id, pr_url, delivery_state, delivery_state_at, "
            "created_at, started_at, finished_at, total_cost_usd, resume_attempts, pipeline_template, area) "
            "VALUES ($1, $2, $3, 'pr_merged', $4, $5, 'merged', $6, $7, $7, $6, 1.0, 1, 'map-areas', 'core')",
            f"job-{tag}-kinds",
            repo,
            title,
            f"iss-{tag}-child",
            f"https://github.com/{repo}/pull/77",
            at,
            at - timedelta(hours=2),
        )
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, error_code, created_at, finished_at) "
            "VALUES ($1, $2, $3, 'failed', 'ERR_VERIFY_NEW_TEST_FAILURES', $4, $4)",
            f"job-{tag}-gate",
            repo,
            title,
            at,
        )
        await db.execute(
            "INSERT INTO traces (trace_id, job_id, agent_type, model, result, created_at) "
            "VALUES ($1, $2, 'review', 'm', 'success', $3), ($4, $2, 'qa', 'm', 'success', $3)",
            f"trc-{tag}-review",
            f"job-{tag}-kinds",
            at,
            f"trc-{tag}-qa",
        )
        await db.execute(
            "INSERT INTO issue_inputs (id, issue_id, job_id, question, answer, answered_at, status) "
            "VALUES ($1, $2, $3, $4, 'yes', $5, 'answered')",
            f"in-{tag}",
            f"iss-{tag}-child",
            f"job-{tag}-kinds",
            f"asks {title}?",
            at,
        )
        await db.execute(
            "INSERT INTO audit_log (action, actor, created_at, issue_id, details) "
            "VALUES ('issue.input_created', 'developer', $1, $2, $3::jsonb)",
            at,
            f"iss-{tag}-child",
            {"input_id": f"in-{tag}"},
        )
        await db.execute(
            "INSERT INTO healing_cases (id, repo, origin, stage, title, healed_verdict, merged_at, accepted_at, "
            "deployed_at, verified_at, occurrence_count, tracker_url) "
            "VALUES ($1, $2, 'watcher', 'verified', $3, 'healed', $4, $4, $4, $4, 3, $5)",
            f"hc-{tag}",
            repo,
            title,
            at,
            f"https://github.com/{repo}/issues/9",
        )
        await db.execute(
            "INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by, job_id, created_at) "
            "VALUES ($1, $2, 'issue', 'accept', 'ada', $3, $4)",
            f"issue:iss-{tag}-child",
            repo,
            f"job-{tag}-kinds",
            at,
        )
        await db.execute(
            "INSERT INTO repo_standard_state (repo, owner, kind, mode, score, last_audit_at) "
            "VALUES ($1, 'acme', 'design', 'advise', 90, $2)",
            repo,
            at,
        )
        await db.execute(
            "INSERT INTO pr_reviews (id, repo, number, head_sha, trigger, trigger_key, posted_at) "
            "VALUES ($1, $2, 77, 'abc', 'ready', $1, $3)",
            f"rev-{tag}",
            repo,
            at,
        )
        await db.execute(
            "INSERT INTO repo_setup_proposals (id, repo, status, proposed_yaml, decided_at) "
            "VALUES ($1, $2, 'applied', '', $3)",
            f"setup-{tag}",
            repo,
            at,
        )
        await db.execute("INSERT INTO repos (repo) VALUES ($1) ON CONFLICT DO NOTHING", repo)
        await db.execute(
            "INSERT INTO repo_areas_proposals (id, repo, status, decided_at) VALUES ($1, $2, 'applied', $3)",
            f"areas-{tag}",
            repo,
            at,
        )
    # Work that crosses: acme/widgets' job merged on acme/lib's issue, and its question about it.
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, issue_id, pr_url, delivery_state, delivery_state_at, "
        "created_at, started_at, finished_at) "
        "VALUES ('job-w-on-l', 'acme/widgets', 'w', 'pr_merged', 'iss-l-child', $1, 'merged', $2, $2, $2, $2)",
        "https://github.com/acme/widgets/pull/78",
        at,
    )
    await db.execute(
        "INSERT INTO issue_inputs (id, issue_id, job_id, question, answer, answered_at, status) "
        "VALUES ('in-w-on-l', 'iss-l-child', 'job-w-on-l', $1, 'yes', $2, 'answered')",
        f"asks {_THEIRS} again?",
        at,
    )
    # A merged job of each repository that names it in its git context alone, in another letter case.
    for repo, title in (("Acme/Widgets", "widgets-context"), ("Acme/Lib", f"{_THEIRS}-context")):
        await db.execute(
            "INSERT INTO jobs (job_id, context, task, status, pr_url, delivery_state, delivery_state_at, "
            "created_at, started_at, finished_at, total_cost_usd, pipeline_template) "
            "VALUES ($1, $2::jsonb, $3, 'pr_merged', $4, 'merged', $5, $5, $5, $5, 2.0, 'pr-followup')",
            f"job-{repo}-context",
            {"type": "git", "repo": repo},
            title,
            f"https://github.com/{repo}/pull/79",
            at,
        )
        await db.execute(
            "INSERT INTO traces (trace_id, job_id, agent_type, model, result, created_at) "
            "VALUES ($1, $2, 'developer', 'm', 'success', $3)",
            f"trc-{repo}-context",
            f"job-{repo}-context",
            at,
        )


async def _keep_only_widgets(db: DatabasePool) -> None:
    """Every row a deployment holding only acme/widgets would not have, gone, the references to them left dangling."""
    rows = await db.fetch(
        "SELECT conrelid::regclass::text AS tbl, conname FROM pg_constraint WHERE contype = 'f' "
        "AND confrelid::regclass::text IN ('issues', 'jobs', 'healing_cases', 'issue_inputs')"
    )
    for row in rows:
        await db.execute(f'ALTER TABLE {row["tbl"]} DROP CONSTRAINT "{row["conname"]}"')
    mine = "lower({}) IS NOT DISTINCT FROM 'acme/widgets'"
    jobs = mine.format("COALESCE(repo, context->>'repo')")
    await db.execute(f"DELETE FROM traces WHERE job_id NOT IN (SELECT job_id FROM jobs WHERE {jobs})")
    await db.execute(f"DELETE FROM jobs WHERE NOT ({jobs})")
    for table in (
        "issues",
        "healing_cases",
        "proposal_decisions",
        "repo_standard_state",
        "pr_reviews",
        "repo_setup_proposals",
        "repo_areas_proposals",
    ):
        await db.execute(f"DELETE FROM {table} WHERE NOT ({mine.format('repo')})")
    await db.execute("DELETE FROM healing_case_events WHERE case_id NOT IN (SELECT id FROM healing_cases)")
    await db.execute(
        "DELETE FROM issue_inputs WHERE job_id NOT IN (SELECT job_id FROM jobs) "
        "OR (issue_id IS NOT NULL AND issue_id NOT IN (SELECT id FROM issues))"
    )
    await db.execute(
        "DELETE FROM audit_log WHERE action = 'issue.input_created' "
        "AND NOT EXISTS (SELECT 1 FROM issue_inputs q WHERE q.id = details->>'input_id')"
    )


@pytest.mark.changes_schema
async def test_every_fact_event_and_journal_row_of_a_scope_is_a_deployment_of_its_own_s(
    db_with_migrations: DatabasePool,
) -> None:
    """Each fact fragment the code has (whatever the pack counts), every event and the journal, read for acme/widgets'
    scope, are exactly what a deployment holding only acme/widgets's rows reads, work that crosses into acme/lib
    included: its issue's title is not acme/widgets', nor its question."""
    import string

    from embry0.storage.repositories.progression import FRAGMENTS

    db = db_with_migrations
    await _history(db, 5, datetime(2025, 12, 20, 9, tzinfo=UTC))
    await _every_kind(db)
    queries = []
    for name, template in FRAGMENTS.items():
        needs = {field for _, field, _, _ in string.Formatter().parse(template) if field and field != "tz"}
        queries.append(_Fragment(name, name, {k: _PARAMS[k] for k in needs} | _OVER.get(name, {})))
    repo = ProgressionRepository(db)

    async def everything(repos: list[str] | None) -> tuple[Any, ...]:
        facts = await (repo.facts(queries, "UTC", repos=repos) if repos else repo.facts(queries, "UTC"))
        events = await (repo.events(repos=repos) if repos else repo.events())
        journal = await (repo.journal(None, repos=repos) if repos else repo.journal(None))
        return sorted(map(repr, facts)), list(map(repr, events)), journal

    scoped = await everything(SCOPE)
    everyone = await everything(None)
    assert any(_THEIRS in f for f in everyone[0]), "acme/lib's rows are in the facts read whole: the test measures"
    assert not any(_THEIRS in f for f in scoped[0]), [f for f in scoped[0] if _THEIRS in f]
    assert any("Acme/Widgets #79" in f for f in scoped[0]), "the job naming its repository in its git context alone"
    assert not any("acme/lib" in f.lower() for f in scoped[0]), [f for f in scoped[0] if "acme/lib" in f.lower()]
    await _keep_only_widgets(db)
    alone = await everything(None)
    for part, name in enumerate(("facts", "events", "journal")):
        assert scoped[part] == alone[part], (
            f"the scope's {name} differ from a deployment of acme/widgets alone: "
            f"{sorted(set(map(str, scoped[part])) ^ set(map(str, alone[part])))[:6]}"
        )
    metrics = {f.split("metric='")[1].split("'")[0] for f in scoped[0]}
    assert metrics == set(FRAGMENTS), f"every fragment selects the scope's rows: {sorted(set(FRAGMENTS) - metrics)}"
