"""A job's log tool reads only its own repository's containers, whatever its ticket's evidence names.

The developer log tool (``DEVELOPER_LOG_TOOL_ENABLED``) is scoped to the
containers a job's evidence names. The evidence is the ticket's text, which
whoever starts the job writes, so the names it carries are read against the
containers of the job's own repository: those its watched stack names as its
own (``context_proxy_container_allowlist``) and the deployment allowlists. A
name another repository's stack owns, or one no stack owns, grants nothing.

On the real app over a real database (:mod:`tests.fixtures.repository_writes`):
a key limited to ``acme/web`` starts a job there whose evidence names
``globex/gizmos``' container.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from embry0.api.deps import bust_api_key_cache
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from embry0.workflows.issue_to_pr.evidence import log_scope
from embry0.workflows.issue_to_pr.nodes._init import _job_case_evidence
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: The deployment's context-proxy allowlist: a container of each repository.
DEPLOYMENT = SimpleNamespace(context_proxy_container_allowlist="acme-web,globex-gizmos-api")
CONTAINER = {COVERED: "acme-web", UNCOVERED: "globex-gizmos-api"}


def _task(container: str) -> str:
    return f"Fix the checkout error.\n\n## Evidence\n\n{container} ERROR payment refused for order 12\n"


async def _scope(app: Any, db: DatabasePool, token: str, container: str) -> list[str]:
    """The containers a job the key starts on its repository, with evidence naming ``container``, may query."""
    made = await call(app, "POST", "/api/v1/jobs", token, {"repo": COVERED, "task": _task(container)})
    assert made.status_code in (200, 201, 202), made.text
    job_id = str(made.json().get("job_id") or made.json().get("id"))
    stored = await db.fetchrow("SELECT repo, task, context FROM jobs WHERE job_id = $1", job_id)
    assert stored is not None and stored["repo"] == COVERED
    state = {"repo": stored["repo"], "task": stored["task"], "context": stored["context"], "issue_id": None}
    evidence = await _job_case_evidence(state, {"db": db}, DEPLOYMENT, job_id)
    return log_scope(evidence)


async def _own(db: DatabasePool, repo: str, containers: str | None) -> None:
    settings = {} if containers is None else {"context_proxy_container_allowlist": containers}
    await db.execute("UPDATE watcher_stacks SET settings = $2 WHERE repo = $1", repo, settings)


async def test_a_job_s_log_tool_reads_only_its_repository_s_containers(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, _keys, _values):
        _row, operate = await ApiKeysRepository(db).create(
            name="dispatch", scope="operate", user_id=None, created_by="tests", allowed_repos=[COVERED]
        )
        bust_api_key_cache()
        await _own(db, COVERED, CONTAINER[COVERED])
        await _own(db, UNCOVERED, CONTAINER[UNCOVERED])
        elsewhere = await _scope(app, db, operate, CONTAINER[UNCOVERED])
        own = await _scope(app, db, operate, CONTAINER[COVERED])
        # A stack that names no containers of its own owns none of the deployment's.
        await _own(db, COVERED, None)
        unowned = await _scope(app, db, operate, CONTAINER[COVERED])
    assert elsewhere == [], f"a job on {COVERED} is scoped to {UNCOVERED}'s container: {elsewhere}"
    assert own == [CONTAINER[COVERED]], f"a job is not scoped to its own repository's container: {own}"
    assert unowned == [], f"a stack naming no containers grants the deployment's: {unowned}"


#: An analysis job's context as the watcher wrote it when the stack named more containers, or none of its own.
_ANALYSED = {"type": "none", "watcher_log_access": True, "watcher_log_containers": ["acme-web", "globex-gizmos-api"]}


async def test_an_analysis_job_started_again_reads_only_what_its_stack_names_now(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run again copies an analysis job's context, the containers its tick chose with it. Those are a ceiling: the
    new job's sandbox is enrolled with what its stack's stored list admits now (init_node), read off the database
    as production reads it."""
    from embry0.services.job_log_access import analysis_job_scope

    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        monkeypatch.setattr(app.state.issue_executor, "run_again", lambda *_a, **_kw: None)
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, context, launch, created_at, finished_at)"
            " VALUES ('job-analysis-1', $1, 'analyse', 'completed', 'analysis-default', $2, $3, now(), now())",
            COVERED,
            _ANALYSED,
            {"runner": "workflow", "repo": COVERED, "title": "log analysis", "body": "analyse"},
        )
        await _own(db, COVERED, CONTAINER[COVERED])
        made = await call(app, "POST", "/api/v1/jobs/job-analysis-1/run-again", keys.limited, {})
        assert made.status_code == 202, made.text
        new = await db.fetchrow("SELECT repo, context FROM jobs WHERE job_id = $1", made.json()["followup_job_id"])
        assert new is not None and new["context"] == _ANALYSED, "run again no longer copies the context"
        chosen = new["context"]["watcher_log_containers"]
        narrowed = await analysis_job_scope(new["repo"], chosen, {"db": db}, DEPLOYMENT)
        # A stack with no list of its own: what the tick chose, cut to the deployment's allowlist only.
        await _own(db, COVERED, None)
        unowned = await analysis_job_scope(new["repo"], chosen, {"db": db}, DEPLOYMENT)
    assert narrowed == [CONTAINER[COVERED]], f"a job started again reads past its stack's list: {narrowed}"
    assert unowned == ["acme-web", "globex-gizmos-api"], unowned


class _Sandbox:
    def __init__(self) -> None:
        self.scopes: list[Any] = []
        self.runtime = MagicMock()

    async def reenroll(self, _container: str, **kwargs: Any) -> None:
        self.scopes.append(kwargs["context_scope"])


@pytest.mark.parametrize(
    "checkpoint",
    [
        {"case_log_containers": ["acme-web", "globex-gizmos-api"]},
        {"watcher_log_tool": True, "context": _ANALYSED},
    ],
    ids=["developer", "analysis"],
)
async def test_a_resume_reads_the_stored_stack_through_the_graph_s_configurable(
    db_with_migrations: DatabasePool, checkpoint: dict[str, Any]
) -> None:
    """The resume works the scope out again from the stored stack, read through the configurable the executor builds
    for the graph (the one init_node reads), over its database: a configurable that did not carry it would give every
    resumed job no log tool, and one that the resume did not read would leave it trusting the checkpoint."""
    from embry0.config import Embry0Config
    from embry0.services._executor_run import _stream_workflow
    from embry0.services.issue_executor import IssueExecutor

    db = db_with_migrations
    await db.execute(
        "INSERT INTO watcher_stacks (repo, enabled, logql, settings) VALUES ($1, true, '{job=\"x\"}', $2)",
        COVERED,
        {"context_proxy_container_allowlist": CONTAINER[COVERED]},
    )
    config = Embry0Config(
        _env_file=None,
        database_url=db._url,  # noqa: SLF001
        context_proxy_container_allowlist=DEPLOYMENT.context_proxy_container_allowlist,
        developer_log_tool_enabled=True,
    )
    sandbox = _Sandbox()
    executor = IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=MagicMock(),
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="",
        db=db,
        config=config,
        sandbox_manager=sandbox,
    )
    executor._forge_for = AsyncMock(return_value="github")  # type: ignore[method-assign]  # noqa: SLF001
    values = {"sandbox_container_id": "cid-1", "repo": COVERED, **checkpoint}
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values=values, next=()))
    workflow = MagicMock()
    workflow.compile = MagicMock(return_value=graph)
    executor._process_stream = AsyncMock(return_value=(None, False, None))  # type: ignore[method-assign]  # noqa: SLF001

    class _Saver:
        async def __aenter__(self) -> _Saver:
            return self

        async def __aexit__(self, *_args: Any) -> bool:
            return False

    with patch("embry0.services._executor_run.checkpointer_context", lambda _url: _Saver()):
        await _stream_workflow(
            executor,
            workflow,
            Command(resume={"choice": "continue_retrying", "guidance": ""}),
            None,
            "job-paused-1",
            "issue-to-pr",
            None,
            executor._build_graph_config("job-paused-1"),  # noqa: SLF001
            {},
            {},
        )
    assert sandbox.scopes == [[CONTAINER[COVERED]]], sandbox.scopes
