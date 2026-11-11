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

import pytest

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
