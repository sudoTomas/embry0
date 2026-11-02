"""Every write route that acts on one job answers a key acting on another repository's job as on a missing job.

On the real app over a real database (:mod:`tests.fixtures.job_reads`). Each
write route whose path names a job is found by the walk of
``test_repository_writes_inventory.py`` and called through an admin key whose
list covers one repository: on the other repository's job, then on a job that
does not exist, with the same body. The two answers must be the same, status,
headers and body, each id put back as ``<job>``, so an answer never tells
whether the job exists. The first is refused all the same: logged and
audited once, naming the repository; the second is not. Through a key
without a list the route must reach the job, so the check measures something.
That the refused call writes nothing is that walk's own check.
"""

from __future__ import annotations

import re

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.job_reads import JOB, MISSING, job_reads_app, same_answer
from tests.fixtures.repository_writes import UNCOVERED, call
from tests.unit.api.test_repository_writes_inventory import BODIES, READS, _app, _refusals, write_routes

pytestmark = pytest.mark.requires_postgres

_PARAMETER = re.compile(r"\{(\w+)(?::\w+)?\}")

#: Every write route whose path names a job, as (method, path); the token routes store nothing and are reads.
ROUTES = [(method, path) for method, path in write_routes(_app()) if "{job_id}" in path and path not in READS]


def test_the_walk_finds_the_routes_that_act_on_a_job() -> None:
    paths = {path for _method, path in ROUTES}
    assert {
        "/api/v1/jobs/{job_id}/cancel",
        "/api/v1/jobs/{job_id}/resume",
        "/api/v1/jobs/{job_id}/done-when",
        "/api/v1/jobs/{job_id}/inputs/{input_id}/answer",
        "/api/v1/jobs/{job_id}/evaluation",
        "/api/v1/jobs/{job_id}/send-back",
        "/api/v1/jobs/{job_id}/run-again",
        "/api/v1/jobs/{job_id}/dismiss",
        "/api/v1/jobs/{job_id}/reject",
        "/api/v1/world/quests/{job_id}/evaluation",
    } <= paths, sorted(paths)
    assert len(ROUTES) >= 14, f"the walk found {len(ROUTES)} routes acting on a job: {ROUTES}"


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
async def test_acting_on_another_repository_s_job_answers_as_on_a_job_that_does_not_exist(
    method: str, path: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with job_reads_app(db, monkeypatch) as (app, keys, values):
        named = values[UNCOVERED]
        body = BODIES.get(path)

        def url(job: str) -> str:
            return _PARAMETER.sub(lambda m: job if m.group(1) == "job_id" else named[m.group(1)], path)

        before = await _refusals(db)
        uncovered = await call(app, method, url(JOB[UNCOVERED]), keys.limited, body)
        refused = (await _refusals(db))[len(before) :]
        missing = await call(app, method, url(MISSING), keys.limited, body)
        after_missing = (await _refusals(db))[len(before) + len(refused) :]
        reached = await call(app, method, url(JOB[UNCOVERED]), keys.open, body)
    differ = same_answer(uncovered, JOB[UNCOVERED], missing, MISSING)
    assert differ is None, f"{method} {path}: another repository's job answers as a missing one: {differ}"
    assert uncovered.status_code == 404, f"{method} {path}: {uncovered.status_code} {uncovered.text[:200]}"
    assert refused == [UNCOVERED], f"{method} {path}: the refusal is audited once, naming {UNCOVERED}: {refused}"
    assert after_missing == [], f"{method} {path}: a missing job's answer audits nothing: {after_missing}"
    assert reached.status_code != 404 and reached.status_code < 500, (
        f"{method} {path}: a key without an allowlist reaches the job: {reached.status_code} {reached.text[:200]}"
    )
