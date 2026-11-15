"""Work that is counted, in one repository and then in two, on the real app over a real database.

The app and its keys are :mod:`tests.fixtures.repository_writes`':
:func:`aggregate_reads_app` starts it with :data:`COVERED`, the repository the
limited key covers, alone. It holds what every earlier read inventory seeds
for one repository and its owner (an item of every kind the write routes
name; its job's events, verification, session, deliverables, QA results and
artifacts in a stand-in store, a live QA workspace, map sends and watcher
ticks, :mod:`tests.fixtures.job_reads`; its registration, test setup,
variables, credentials, settings layer, discovery facts, standard and maps,
:mod:`tests.fixtures.repository_reads`), and :func:`plant`'s work: what a
total, a count or a sum reads. Jobs of most statuses and deliveries, their
costs, a job whose repository is only in its git context, the background
features' jobs and model calls, agents' runs with their tokens, cost and tool
calls, an issue with a review, a QA pass and a merge on it, ratings, a
pre-built QA image and a forge credential bound to the repository, and a
merged job with its runs on a day long closed, which the progression's rollup
folds (:data:`LONG_AGO`).

:meth:`Deployment.add_uncovered` then gives :data:`UNCOVERED` and its owner
the same, with other numbers and earlier times, and work of kinds the covered
repository has none of (:data:`ONLY_UNCOVERED`): failed jobs, an onboarding
and a retry merged after a failure, so it completes quests the covered
repository does not, and runs of an agent only it uses. It also gives the
covered repository's issue a job of the uncovered repository
(:data:`CROSSING`), plants a job, a run and a model call of no repository
(:func:`plant_beyond`), which no key with an allowlist counts, and forgets
what the app caches, so a read after it reads the rows as they are. What the
limited key reads before it is what a deployment holding only the covered
repository shows; a read that counts the uncovered repository's work answers
the limited key differently after it.

The QA base image's registry is a stand-in (:class:`Digests`), and the shipped
content pack is stored as a draft, so the preview of a draft answers. What
:func:`plant` plants for a repository carries its :data:`MARK`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import embry0.services
from embry0.api.middleware.rate_limit import RateLimitMiddleware
from embry0.orchestration.dispatch_actors import CONTEXT_AUDIT, SCOUT, WATCHER
from embry0.qa.event_bus import QAEventBus
from embry0.services.slop import SLOPMASTER
from embry0.services.world import WorldCache
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.asks import AsksRepository
from embry0.storage.repositories.audit_jobs import AUDITOR
from embry0.storage.repositories.progression import QA_PIPELINE
from embry0.storage.repositories.watcher_runs import WatcherRunsRepository
from tests.fixtures.job_reads import JOB, STORE_HOST, ArtifactStore, Workspaces, _context_audits, _seed_job
from tests.fixtures.job_reads import MARK as JOB_MARK
from tests.fixtures.repository_reads import HOST, OWNERS, _ok, _seed_owner, _seed_repository
from tests.fixtures.repository_reads import MARK as REPO_MARK
from tests.fixtures.repository_writes import (
    COVERED,
    MASTER,
    NOW,
    TAG,
    UNCOVERED,
    Keys,
    call,
    repository_writes_app,
    seed,
)

#: What everything planted for each repository carries.
MARK = {COVERED: "widget-total", UNCOVERED: "gizmo-total"}
#: What each repository's planted jobs cost, apiece: different, so a sum shows whose jobs it adds.
COST = {COVERED: 1.25, UNCOVERED: 7.5}
#: How long before :data:`NOW` each repository's dated work happened: the uncovered repository's first, so a
#: quest that takes the first repository to reach a step takes it while every repository is read.
AGO = {COVERED: timedelta(hours=3), UNCOVERED: timedelta(hours=9)}
#: How long before :data:`NOW` each repository's latest run was: the uncovered repository's last.
LATEST = {COVERED: timedelta(minutes=30), UNCOVERED: timedelta(minutes=1)}
#: The digest the QA base image has now.
BASE_DIGEST = "sha256:base-now"
#: Work that crosses into the covered repository from the uncovered one, and work of no repository.
CROSSING = "job-sum-gizmo-on-widget"
NO_REPOSITORY = "job-sum-nowhere"
#: The content pack embry0 ships, stored as a draft so the preview answers.
DEFAULT_PACK = Path(embry0.services.__file__).parent / "progression_pack_default.yaml"

#: An agent whose runs only the uncovered repository has; it is defined, so its character exists either way.
ONLY_AGENT = "planner"
#: The planted jobs only the uncovered repository has: the failures, so no covered work ever failed.
ONLY_UNCOVERED = ("failed", "scout")

#: The jobs a repository gets: (suffix, status, delivery state, columns); the covered one none of ONLY_UNCOVERED.
JOBS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("merged", "completed", "merged", {}),
    ("open", "completed", "pr_open", {}),
    ("failed", "failed", "none", {"error_code": "ERR_AGENT_TIMEOUT"}),
    ("partial", "partial", "none", {}),
    ("running", "running", "none", {}),
    ("pending", "pending", "none", {}),
    ("queued", "queued", "none", {}),
    ("asking", "awaiting_input", "none", {}),
    ("paused", "paused", "none", {}),
    ("cancelled", "cancelled", "none", {}),
    ("skipped", "skipped", "none", {}),
    ("cleanup", "pr_merged", "merged", {"coding_agent": SLOPMASTER}),
    ("closed", "pr_closed", "closed", {}),
    ("watcher", "completed", "none", {"dispatched_by": WATCHER}),
    ("audit", "completed", "none", {"dispatched_by": CONTEXT_AUDIT, "leader_agent": AUDITOR}),
    ("scout", "failed", "none", {"dispatched_by": SCOUT, "error_code": "ERR_BUDGET_OVERRUN"}),
    ("qa", "completed", "none", {"pipeline_template": QA_PIPELINE}),
    ("resumed", "completed", "none", {"resume_attempts": 1}),
)
#: Terminal statuses: a job in one has finished.
_FINISHED = {"completed", "failed", "partial", "cancelled", "skipped", "pr_merged", "pr_closed"}


def job_id(repo: str, suffix: str) -> str:
    return f"job-sum-{TAG[repo]}-{suffix}"


def issue_id(repo: str) -> str:
    """The issue of ``repo`` that its failed, merged and reviewed jobs work on."""
    return f"iss-sum-{TAG[repo]}"


#: The job of each repository whose repository is in its git context alone, and the one a retry merged on its issue.
CONTEXT_ONLY = "context"
RETRY = "retry"


async def _job(
    db: DatabasePool, repo: str, suffix: str, status: str, delivery: str, columns: Mapping[str, Any]
) -> None:
    # Each job a second apart, in the order JOBS lists them, so no list ordered by time ties.
    order = next((n for n, planted in enumerate(JOBS) if planted[0] == suffix), len(JOBS))
    created = NOW - AGO[repo] - timedelta(minutes=5, seconds=order)
    finished = NOW - AGO[repo] - timedelta(seconds=order) if status in _FINISHED else None
    values: dict[str, Any] = {
        "job_id": job_id(repo, suffix),
        "repo": repo,
        "task": f"{suffix} of {MARK[repo]}",
        "status": status,
        "pipeline_template": "full-magnum-opus",
        "issue_id": issue_id(repo) if suffix in ("merged", "failed", "open") else None,
        "created_at": created,
        "started_at": created,
        "finished_at": finished,
        "updated_at": finished or created,
        "total_cost_usd": COST[repo],
        "delivery_state": delivery,
        "delivery_state_at": finished if delivery != "none" else None,
        "pr_url": f"https://github.com/{repo}/pull/{len(suffix) + 40}" if delivery != "none" else None,
        "dispatched_by": "ada@example.com",
        "result_summary": f"report of {MARK[repo]}",
        **columns,
    }
    names = ", ".join(values)
    marks = ", ".join(f"${n}" for n in range(1, len(values) + 1))
    await db.execute(f"INSERT INTO jobs ({names}) VALUES ({marks})", *values.values())


async def _run(
    db: DatabasePool, repo: str, suffix: str, agent: str, n: int, *, error: bool = False, at: datetime | None = None
) -> None:
    """The ``n``th run of ``repo``'s planted work: each costs its own amount, so no two agents' totals tie."""
    await db.execute(
        "INSERT INTO traces (trace_id, job_id, agent_type, model, result, cost_usd, duration_ms, tools_called, "
        "result_summary, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, error_code, "
        "stderr_tail, skill_actions, created_at) "
        "VALUES ($1, $2, $3, 'claude-test', $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13, $14, $15::jsonb, $16)",
        f"trc-sum-{TAG[repo]}-{suffix}-{agent}-{n}",
        job_id(repo, suffix),
        agent,
        "error" if error else "success",
        COST[repo] * (n + 1) / 8,
        int(COST[repo] * 1000),
        {"Bash": int(COST[repo] * 4), "Read": 2},
        f"run of {MARK[repo]}",
        int(COST[repo] * 1000),
        int(COST[repo] * 100),
        int(COST[repo] * 300),
        int(COST[repo] * 10),
        "ERR_AGENT_TIMEOUT" if error else None,
        f"stderr of {MARK[repo]}" if error else None,
        {"edit:code": 1},
        at or NOW - AGO[repo] - timedelta(minutes=5) + timedelta(seconds=n),
    )


async def plant(app: FastAPI, db: DatabasePool, repo: str) -> None:
    """``repo``'s work, of every kind a total counts."""
    mark = MARK[repo]
    await db.execute(
        "INSERT INTO issues (id, title, body, repo, status, tracker, tracker_key, tracker_url, created_at) "
        "VALUES ($1, $2, $2, $3, 'open', 'github', '41', $4, $5)",
        issue_id(repo),
        f"costly {mark}",
        repo,
        f"https://github.com/{repo}/issues/41",
        NOW - AGO[repo] - timedelta(hours=1),
    )
    for suffix, status, delivery, columns in JOBS:
        if repo == UNCOVERED or suffix not in ONLY_UNCOVERED:
            await _job(db, repo, suffix, status, delivery, columns)
    if repo == UNCOVERED:
        await _onboarded_and_retried(db, repo)
    await db.execute(
        "INSERT INTO jobs (job_id, task, status, context, created_at, started_at, finished_at, total_cost_usd) "
        "VALUES ($1, $2, 'completed', $3::jsonb, $4, $4, $4, $5)",
        job_id(repo, CONTEXT_ONLY),
        f"context only of {mark}",
        {"type": "git", "repo": repo, "ref": "main"},
        NOW - AGO[repo],
        COST[repo],
    )
    for n, agent in enumerate(("developer", "review", "qa", "triage")):
        await _run(db, repo, "merged", agent, n)
    if repo == UNCOVERED:
        await _run(db, repo, "failed", "developer", 4, error=True)
        await _run(db, repo, "merged", ONLY_AGENT, 7)
    await _run(db, repo, CONTEXT_ONLY, "developer", 5)
    # The latest run: the uncovered repository's is the newer, so a character's last quest is its.
    await _run(db, repo, "open", "developer", 6, at=NOW - LATEST[repo])
    await db.execute(
        "INSERT INTO job_ratings (job_id, rating, verdict, rated_by) VALUES ($1, 'good', 'merged', 'ada'), "
        "($2, 'needs_work', 'sent_back', 'ada')",
        job_id(repo, "merged"),
        job_id(repo, "closed"),
    )
    await _counted_beside_jobs(app, db, repo)
    await _long_ago(db, repo)


#: How long before :data:`NOW` each repository's work of a closed day happened: a day the rollup folds.
LONG_AGO = {COVERED: timedelta(days=4), UNCOVERED: timedelta(days=5)}


async def _long_ago(db: DatabasePool, repo: str) -> None:
    """``repo``'s merged job of a day long closed, and its runs: what the progression's rollup folds once, so a read
    resumes from it."""
    at = NOW - LONG_AGO[repo]
    await _job(
        db,
        repo,
        "long-ago",
        "pr_merged",
        "merged",
        {"created_at": at, "started_at": at, "finished_at": at, "updated_at": at, "delivery_state_at": at},
    )
    for n, agent in enumerate(("developer", "review", "developer", "triage")):
        await _run(db, repo, "long-ago", agent, 20 + n, at=at + timedelta(minutes=n))


async def _onboarded_and_retried(db: DatabasePool, repo: str) -> None:
    """``repo``'s onboarding, before the rest of its work, and a retry merged after the failure on its issue: the
    First Steps and the Try, Try Again quests, done."""
    onboarded = NOW - AGO[repo] - timedelta(hours=2)
    await _job(
        db,
        repo,
        "onboard",
        "completed",
        "none",
        {"pipeline_template": "onboard", "created_at": onboarded, "started_at": onboarded, "finished_at": onboarded},
    )
    retried = NOW - AGO[repo]
    await _job(
        db,
        repo,
        RETRY,
        "pr_merged",
        "merged",
        {
            "issue_id": issue_id(repo),
            "created_at": retried,
            "started_at": retried,
            "finished_at": retried + timedelta(minutes=1),
            "updated_at": retried + timedelta(minutes=1),
            "delivery_state_at": retried + timedelta(minutes=1),
        },
    )


async def _counted_beside_jobs(app: FastAPI, db: DatabasePool, repo: str) -> None:
    """``repo``'s background model call and pre-built QA image; the uncovered repository's forge credential is
    quarantined."""
    await db.execute(
        "INSERT INTO background_spend (at, feature, model, repo, input_tokens, output_tokens, cost_usd) "
        "VALUES ($1, 'job_brief', 'claude-test', $2, $3, $4, $5)",
        NOW - timedelta(minutes=2),
        repo,
        int(COST[repo] * 100),
        int(COST[repo] * 10),
        COST[repo] / 4,
    )
    # The covered repository's pre-built image is on the current base, the uncovered one's on an older one.
    await db.execute(
        "INSERT INTO qa_image_tags (repo, image_tag, lockfile_sha, is_active, base_image, base_image_digest) "
        "VALUES ($1, $2, 'sha', true, 'registry.test/qa-base:1', $3)",
        repo,
        f"qa-{TAG[repo]}:1",
        BASE_DIGEST if repo == COVERED else "sha256:base-old",
    )
    if repo == UNCOVERED:
        # The forge credential tests.fixtures.repository_reads bound to the repository, quarantined.
        await db.execute("UPDATE credentials SET status = 'quarantined' WHERE label = $1", f"{REPO_MARK[repo]}-forge")


class Digests:
    """The registry's answer for the QA base image: its digest now."""

    async def current(self, image: str) -> str | None:
        return BASE_DIGEST


async def plant_beyond(db: DatabasePool) -> None:
    """A job of the uncovered repository on the covered repository's issue (an issue that moved after its job ran),
    the newest and costliest of the issue's, and a job, an agent's run and a model call of no repository."""
    await _job(
        db,
        UNCOVERED,
        "on-widget",
        "failed",
        "none",
        {
            "job_id": CROSSING,
            "issue_id": issue_id(COVERED),
            "created_at": NOW,
            "started_at": NOW,
            "finished_at": NOW,
            "updated_at": NOW,
            "total_cost_usd": 99.0,
        },
    )
    await db.execute(
        "INSERT INTO jobs (job_id, task, status, context, created_at, started_at, finished_at, total_cost_usd, "
        "error_code) VALUES ($1, 'of no repository', 'failed', $2::jsonb, $3, $3, $3, 11.0, 'ERR_AGENT_TIMEOUT')",
        NO_REPOSITORY,
        {"type": "http", "url": "https://example.test/page"},
        NOW - timedelta(minutes=3),
    )
    await db.execute(
        "INSERT INTO traces (trace_id, job_id, agent_type, model, result, cost_usd, input_tokens, created_at) "
        "VALUES ('trc-sum-nowhere', $1, 'developer', 'claude-test', 'success', 5.0, 777, $2)",
        NO_REPOSITORY,
        NOW - timedelta(minutes=3),
    )
    await db.execute(
        "INSERT INTO background_spend (at, feature, model, input_tokens, cost_usd) "
        "VALUES ($1, 'job_brief', 'claude-test', 50, 0.5)",
        NOW - timedelta(minutes=2),
    )


def forget_caches(app: FastAPI) -> None:
    """Drop what the app keeps between reads (the world's rows, the progression), so the next read reads the rows."""
    app.state.world_cache = WorldCache()
    progression = getattr(app.state, "progression", None)
    if progression is not None:
        progression._cached.clear()  # noqa: SLF001 - the service's own caches, each kept a minute


@dataclass
class Deployment:
    """The app, its keys and each seeded repository's ids; :data:`UNCOVERED` joins it on :meth:`add_uncovered`."""

    app: FastAPI
    db: DatabasePool
    keys: Keys
    values: dict[str, dict[str, str]] = field(default_factory=dict)

    store: ArtifactStore = field(default_factory=ArtifactStore)

    async def add_uncovered(self) -> None:
        self.values.update(await seed(self.db, (UNCOVERED,)))
        self.values[UNCOVERED]["credential_id"] = self.values[COVERED]["credential_id"]
        await _every_read_s(self.app, self.db, UNCOVERED, self.store)
        await plant(self.app, self.db, UNCOVERED)
        await plant_beyond(self.db)
        forget_caches(self.app)


async def _every_read_s(app: FastAPI, db: DatabasePool, repo: str, store: ArtifactStore) -> None:
    """What the earlier read inventories seed for ``repo`` and its owner."""
    await _seed_job(app, db, repo, store)
    await _seed_repository(app, db, repo)
    await _seed_owner(app, db, OWNERS[repo])


@asynccontextmanager
async def aggregate_reads_app(db: DatabasePool, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Deployment]:
    """The app with :data:`COVERED`'s work alone; :meth:`Deployment.add_uncovered` adds :data:`UNCOVERED`'s."""
    # A walk of every route reads far more than one key's minute of requests; the limiter is not what is read here.
    monkeypatch.setattr(RateLimitMiddleware, "_over_limit", lambda _self, _key, _max_rpm: False)
    async with repository_writes_app(db, monkeypatch, (COVERED,)) as (app, keys, values):
        deployment = Deployment(app, db, keys, {repo: dict(v) for repo, v in values.items()})
        store = deployment.store
        # What the production lifespan adds beside _init_app_state, as tests.fixtures.job_reads stands it in.
        app.state.qa_minio = store
        app.state.qa_event_bus = QAEventBus()
        app.state.watcher_runs_repo = WatcherRunsRepository(app.state.db)
        app.state.context_audit = _context_audits(app)
        app.state.runtime = Workspaces({JOB[repo]: JOB_MARK[repo] for repo in (COVERED, UNCOVERED)})
        app.state.qa_base_digest_resolver = Digests()

        async def artifacts_only(_transport: Any, request: httpx.Request) -> httpx.Response:
            if request.url.host == STORE_HOST:
                return store.serve(request)
            raise httpx.ConnectError("tests reach no network")

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", artifacts_only)
        await db.execute(
            "INSERT INTO hosts (id, name, address, transport) VALUES ($1, 'shared-1', '10.0.0.1', 'ssh')", HOST
        )
        await db.execute(
            "INSERT INTO agent_definitions (type, description, model) VALUES ($1, 'plans', 'claude-test')", ONLY_AGENT
        )
        await _every_read_s(app, db, COVERED, store)
        await plant(app, db, COVERED)
        # A quest on offer and its ask's code, for the routes that read one: the issue the fixture offered has work.
        covered = deployment.values[COVERED]
        covered["proposal_id"] = str((await _ok(app, "GET", "/api/v1/proposals"))["proposals"][0]["id"])
        covered["code"] = await AsksRepository(db).code(f"offer:{covered['proposal_id']}")
        await _ok(
            app, "PUT", "/api/v1/environment/global", {"variables": [{"key": "API_URL", "value": "https://x.test"}]}
        )
        # A draft of the content pack, the shipped one as it is, so the preview of a draft answers.
        draft = await call(app, "PUT", "/api/v1/progression/pack/draft", MASTER, DEFAULT_PACK.read_text())
        assert draft.status_code == 200, draft.text
        # The first read of the effective settings imports the environment's values into the deployment's
        # document; done here, so no read under test sees the document change between two of its own.
        await _ok(app, "GET", "/api/v1/config/settings/effective")
        forget_caches(app)
        yield deployment
