"""Two repositories' jobs, with something of every kind the job routes read, on the real app over a real database.

The app, its keys and its work are :mod:`tests.fixtures.repository_writes`':
one finished job in :data:`COVERED`, the repository the limited key covers,
and one in :data:`UNCOVERED` (:data:`JOB`). :func:`job_reads_app` gives each
job what the job routes read besides its row: pipeline events, a
verification, an agent's stored session, two deliverables (a report and an
artifact with stored bytes), a trace, a QA result and the run's metadata,
QA artifacts in a stand-in store (a result, a screenshot, a service's log, a
QA sub-task's screenshot), a live QA workspace in a stand-in runtime with
one service and its log, and its sandbox containers.

Everything stored for a job carries its repository's :data:`MARK`, so an
answer that shows any of the uncovered job is found
(:func:`shows_uncovered_job`). :data:`MISSING` is a job id that names
nothing and is as long as the uncovered job's, so the two answers compare
byte for byte once each id is put back as ``<job>`` (:func:`same_answer`).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from embry0.qa.event_bus import QAEventBus
from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, NOW, UNCOVERED, Keys, ids, repository_writes_app

#: Each repository's finished job.
JOB = {repo: ids(repo)["job_id"] for repo in (COVERED, UNCOVERED)}
#: A job id that names no job, as long as the uncovered repository's.
MISSING = "job-none-gizmo"
#: What everything stored for each repository's job carries.
MARK = {COVERED: "widget-job-content", UNCOVERED: "gizmo-job-content"}
#: The QA app (and the workspace's service) of each job.
APP = "web"
#: Where the stand-in artifact store's presigned URLs point.
STORE_HOST = "artifacts.test"
BUCKET = "qa-artifacts"

assert len(MISSING) == len(JOB[UNCOVERED]), "the missing id is as long as the uncovered job's"


def deliverable_id(repo: str) -> str:
    """The id of ``repo``'s job's artifact deliverable."""
    return f"dlv-{ids(repo)['job_id']}"


def screenshot(repo: str) -> str:
    return f"{JOB[repo]}/1/screenshots/final/1-home.png"


class ArtifactStore:
    """The QA artifact store (``qa_minio``) in memory, by bucket and key; a presigned URL points at
    :data:`STORE_HOST`, which :meth:`serve` answers."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put(self, key: str, body: bytes, bucket: str = BUCKET) -> None:
        self.objects[(bucket, key)] = body

    async def list_objects(self, bucket: str, prefix: str = "") -> list[str]:
        return sorted(k for b, k in self.objects if b == bucket and k.startswith(prefix))

    async def list_objects_with_meta(self, bucket: str, prefix: str = "") -> list[dict[str, Any]]:
        return [{"key": k, "last_modified": NOW} for k in await self.list_objects(bucket, prefix)]

    async def stat_object(self, bucket: str, key: str) -> dict[str, Any]:
        if (bucket, key) not in self.objects:
            raise FileNotFoundError(key)
        return {"size": len(self.objects[(bucket, key)])}

    async def get_object_bytes(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]

    async def presign_get(self, bucket: str, key: str, expires_seconds: int = 300) -> str:
        return f"http://{STORE_HOST}/{bucket}/{key}"

    def serve(self, request: httpx.Request) -> httpx.Response:
        bucket, _, key = request.url.path.lstrip("/").partition("/")
        body = self.objects.get((bucket, key))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404, content=b"NoSuchKey")


class _Lines:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""

    async def read(self, _n: int = -1) -> bytes:
        return b""


@dataclass
class _Process:
    """A finished ``docker compose logs`` process that printed ``lines``."""

    lines: list[bytes]
    returncode: int | None = 0
    stdout: _Lines = field(init=False)

    def __post_init__(self) -> None:
        self.stdout = _Lines(self.lines)

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    async def wait(self) -> int:
        return 0


class Workspaces:
    """The container runtime as the QA and sandbox routes read it: each job's live QA workspace, whose one service
    is named for the job's :data:`MARK` and logs it, and each job's sandboxes. A project with no workspace has no
    services, and its log is the error a missing project gets."""

    def __init__(self, marks: Mapping[str, str]) -> None:
        #: job id -> its workspace's service, which logs its own name.
        self.services = dict(marks)

    async def compose_services(self, project: str, *, timeout: int = 10) -> list[str]:
        service = self.services.get(project.removeprefix("qa_"))
        return [service] if service else []

    async def compose_log_stream(self, project: str, service: str, *, tail: int = 200) -> _Process:
        if self.services.get(project.removeprefix("qa_")) == service:
            return _Process([f"{service} is up\n".encode()])
        return _Process([f"no such service: {service}\n".encode()])

    async def list_containers(self, *, name: str, include_stopped: bool = False, timeout: int = 10) -> list[Any]:
        names = [f"sandbox-{job}" for job in self.services] + [f"sandbox-{job}__{APP}" for job in self.services]
        return [SimpleNamespace(name=n, status="running", running_for="1 minute") for n in sorted(names)]


async def _seed_job(app: FastAPI, db: DatabasePool, repo: str, store: ArtifactStore) -> None:
    job, mark = JOB[repo], MARK[repo]
    await app.state.jobs_repo.append_log_event(job, {"type": "progress", "message": mark})
    await db.execute(
        "INSERT INTO job_verifications (job_id, repo, tier, gate_verdict, browser_qa, report) "
        "VALUES ($1, $2, 'standard', 'pass', 'none', $3::jsonb)",
        job,
        repo,
        {"summary": mark},
    )
    await db.execute(
        "INSERT INTO agent_sessions (id, job_id, agent_type, mode, messages) "
        "VALUES ($1, $2, 'developer', 'sdk', $3::jsonb)",
        f"ses-{job}",
        job,
        [{"role": "assistant", "content": mark}],
    )
    await db.execute(
        "INSERT INTO deliverables (id, job_id, type, title, content) VALUES ($1, $2, 'report', 'Report', $3)",
        f"rpt-{job}",
        job,
        mark,
    )
    await db.execute(
        "INSERT INTO deliverables (id, job_id, type, title, storage_bucket, storage_key, media_type) "
        "VALUES ($1, $2, 'artifact', $3, $4, $5, 'text/plain')",
        deliverable_id(repo),
        job,
        f"{mark}.txt",
        BUCKET,
        f"{job}/deliverables/{mark}.txt",
    )
    store.put(f"{job}/deliverables/{mark}.txt", mark.encode())
    await db.execute(
        "INSERT INTO traces (trace_id, job_id, agent_type, model, result, cost_usd, duration_ms, result_summary) "
        "VALUES ($1, $2, 'developer', 'm', 'success', 1, 1000, $3)",
        f"tr-{job}",
        job,
        mark,
    )
    await db.execute(
        "INSERT INTO qa_app_results (job_id, app_name, status, duration_ms, cache_hits, failure_summary) "
        "VALUES ($1, $2, 'qa_failure', 1000, $3::jsonb, $4)",
        job,
        APP,
        {"prebaked_image": True, "shared_volume": False, "turbo_remote_hits": ["t"], "turbo_remote_misses": []},
        mark,
    )
    await db.execute(
        "INSERT INTO qa_run_metadata (job_id, apps_to_qa, apps_skipped, force_all_apps, changed_files, base_branch, "
        "head_sha) VALUES ($1, ARRAY[$2], ARRAY[]::text[], false, ARRAY[$3], 'main', 'abc123')",
        job,
        APP,
        f"{mark}.py",
    )
    await _link_job(db, repo)
    store.put(f"{job}/1/result.json", json.dumps({"summary": mark}).encode())
    store.put(screenshot(repo), mark.encode())
    store.put(f"{job}/1/logs/{APP}.log", mark.encode())
    store.put(f"{job}__{APP}/1/screenshots/1-home.png", mark.encode())


async def _link_job(db: DatabasePool, repo: str) -> None:
    """Name ``repo``'s job where other work names a job: its issue, its case, a watcher tick, the repository's test
    setup proposal and design standard's last audit, a knowledge map part sent to it; and give the repository a
    context-audit job (:func:`audit_job`)."""
    job, item = JOB[repo], ids(repo)
    await db.execute("UPDATE jobs SET issue_id = $2 WHERE job_id = $1", job, item["issue_id"])
    await db.execute("INSERT INTO healing_case_jobs (case_id, job_id) VALUES ($1, $2)", item["case_id"], job)
    await db.execute(
        "INSERT INTO watcher_runs (id, window_start, window_end, action, job_id, repo) "
        "VALUES ($1, $2, $3, 'analysed', $4, $5)",
        f"wr-{job}",
        NOW - timedelta(hours=2),
        NOW - timedelta(hours=1),
        job,
        repo,
    )
    await db.execute("UPDATE repo_setup_proposals SET job_id = $2 WHERE id = $1", item["setup_proposal_id"], job)
    await db.execute("UPDATE repo_standard_state SET last_audit_job_id = $2 WHERE repo = $1", repo, job)
    await db.execute(
        "INSERT INTO knowledge_map_sends (map_slug, map_owner, node_id, kind, repo, job_id, sent_by) "
        "VALUES ($1, $2, 'n1', 'fix', $3, $4, 'ada')",
        item["slug"],
        item["owner"],
        repo,
        job,
    )
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at) "
        "VALUES ($1, $2, $3, 'completed', 'context-audit', $4, $4, $5)",
        audit_job(repo),
        repo,
        f"Audit {MARK[repo]}",
        NOW - timedelta(hours=3),
        NOW - timedelta(hours=2),
    )


def audit_job(repo: str) -> str:
    """``repo``'s context-audit job, whose id carries its :data:`MARK`."""
    return f"job-{MARK[repo]}"


@asynccontextmanager
async def job_reads_app(
    db: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FastAPI, Keys, Mapping[str, Mapping[str, str]]]]:
    """:func:`tests.fixtures.repository_writes.repository_writes_app`, each job given what the job routes read."""
    async with repository_writes_app(db, monkeypatch) as (app, keys, values):
        store = ArtifactStore()
        for repo in (COVERED, UNCOVERED):
            await _seed_job(app, db, repo, store)
        app.state.qa_minio = store
        # What the production lifespan adds beside _init_app_state: the QA runs' event bus.
        app.state.qa_event_bus = QAEventBus()
        app.state.runtime = Workspaces({JOB[repo]: MARK[repo] for repo in (COVERED, UNCOVERED)})

        async def artifacts_only(_transport: Any, request: httpx.Request) -> httpx.Response:
            if request.url.host == STORE_HOST:
                return store.serve(request)
            raise httpx.ConnectError("tests reach no network")

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", artifacts_only)
        yield app, keys, values


def shows_uncovered_job(text: str) -> bool:
    """Whether an answer shows the uncovered repository's job: its id, or anything stored for it."""
    return JOB[UNCOVERED] in text or MARK[UNCOVERED] in text


def same_answer(a: httpx.Response, a_job: str, b: httpx.Response, b_job: str) -> tuple[Any, Any] | None:
    """None when ``a`` (about ``a_job``) and ``b`` (about ``b_job``) are the same answer: status, headers and body,
    each job's id put back as ``<job>``; else both, for the message."""

    def shown(response: httpx.Response, job: str) -> tuple[int, list[tuple[str, str]], bytes]:
        headers = sorted((k.lower(), v.replace(job, "<job>")) for k, v in response.headers.items())
        return response.status_code, headers, response.content.replace(job.encode(), b"<job>")

    left, right = shown(a, a_job), shown(b, b_job)
    return None if left == right else (left, right)
