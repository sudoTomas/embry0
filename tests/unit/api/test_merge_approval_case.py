"""A merge approval records the healing case its job works, and names it only to a key that reads that case (#407).

The ask reads the case through the real repository, on the real app over a
real database (:mod:`tests.fixtures.repository_writes`): ``find_by_job``
answers the case row, whose id is ``id``. Until #407 the service read a
``case_id`` key the row does not have, so every approval recorded none.

The covered repository has two jobs with an open pull request each: one works
its own repository's case, the other the other repository's, as when a case's
issue moved after its job ran. The approvals routes null a case the key does
not read (:mod:`embry0.api.case_allowlist`, #408), which until now only ever
met approvals whose case was written into the table by hand.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services.merge_approvals import MergeApprovalService
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.merge_approvals import MergeApprovalsRepository
from tests.fixtures.repository_writes import COVERED, NOW, UNCOVERED, call, repository_writes_app
from tests.unit.services.test_merge_approvals import FakeGitHub, _config

pytestmark = pytest.mark.requires_postgres

#: The covered repository's job working its own case, and its job working the other repository's.
OWN_JOB, CROSSING_JOB = "job-own-case", "job-other-case"
#: Each job's pull request in the covered repository.
PULL = {OWN_JOB: 8, CROSSING_JOB: 9}


async def test_an_approval_records_its_job_s_case_and_a_limited_key_reads_only_its_own(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, values):
        own, other = values[COVERED]["case_id"], values[UNCOVERED]["case_id"]
        cases = app.state.healing_cases_repo
        for job_id, case_id in ((OWN_JOB, own), (CROSSING_JOB, other)):
            await db_with_migrations.execute(
                "INSERT INTO jobs (job_id, repo, task, status) VALUES ($1, $2, 'Fix it', 'completed')",
                job_id,
                COVERED,
            )
            async with cases.locked(case_id) as locked:
                assert locked is not None
                await locked.link_job(job_id, NOW)

        service = MergeApprovalService(
            repo=MergeApprovalsRepository(db_with_migrations),
            github=FakeGitHub(),
            notifier=None,
            config=_config(merge_approval_repos=COVERED),
            cases=cases,
        )
        recorded: dict[str, dict[str, Any]] = {}
        for job_id, number in PULL.items():
            row = await service.ask(job_id=job_id, pr_url=f"https://github.com/{COVERED}/pull/{number}")
            assert row is not None, f"no ask recorded for {job_id}"
            recorded[job_id] = row
        assert {job_id: row["case_id"] for job_id, row in recorded.items()} == {OWN_JOB: own, CROSSING_JOB: other}, (
            "each approval records the case its job works"
        )

        seen: dict[tuple[str, str], tuple[Any, Any]] = {}
        for name, token in (("limited", keys.limited), ("open", keys.open)):
            listed = await call(app, "GET", "/api/v1/merge-approvals", token)
            assert listed.status_code == 200, listed.text
            items = {item["id"]: item for item in listed.json()["items"]}
            for job_id, row in recorded.items():
                one = await call(app, "GET", f"/api/v1/merge-approvals/{row['id']}", token)
                assert one.status_code == 200, one.text
                seen[(name, job_id)] = (items[row["id"]]["case_id"], one.json()["case_id"])

    assert seen == {
        ("limited", OWN_JOB): (own, own),
        ("limited", CROSSING_JOB): (None, None),
        ("open", OWN_JOB): (own, own),
        ("open", CROSSING_JOB): (other, other),
    }, f"the limited key reads its own repository's case and not {UNCOVERED}'s, in the list and alone: {seen}"
