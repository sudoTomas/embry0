"""SH-P4.4: init_node's evidence helper against the REAL ledger (Postgres).

The services evidence names are read against the job's repository's own
containers: those its watched stack names (``context_proxy_container_allowlist``)
and the deployment allowlists (:func:`repository_log_containers`).
"""

from __future__ import annotations

from types import SimpleNamespace

from embry0.services.healing.lifecycle import Origin
from embry0.services.healing.service import HealingCaseService
from embry0.workflows.issue_to_pr.nodes._init import _job_case_evidence

from .conftest import REPO, Seed

_CONFIG = SimpleNamespace(context_proxy_container_allowlist="acme-backend, acme-ml")
_TASK = "[ENG-1] Fix\n\n## Evidence\nacme-backend: 502 Bad Gateway x4\n"
_STATE = {"task": _TASK, "repo": REPO}


async def _stack(seed: Seed, containers: str = "acme-backend, acme-ml") -> None:
    """The repository's watched stack, naming its containers as its own."""
    await seed.db.execute(
        "INSERT INTO watcher_stacks (repo, logql, settings) VALUES ($1, '{app=\"x\"}', $2)",
        REPO,
        {"context_proxy_container_allowlist": containers},
    )


async def test_linked_case_is_the_source(service: HealingCaseService, seed: Seed) -> None:
    job_id = await seed.job(await seed.issue())
    case = await service.open_case(repo=REPO, origin=Origin.WATCHER, title="boom", signature='{app="x"} |= "502"')
    await service.attach_job(case["id"], job_id)
    await _stack(seed)

    evidence = await _job_case_evidence(_STATE, {"db": seed.db}, _CONFIG, job_id)

    assert evidence == {
        "source": "case",
        "case_id": case["id"],
        "signature": '{app="x"} |= "502"',
        "samples": ["acme-backend: 502 Bad Gateway x4"],
        "services": ["acme-backend"],
    }


async def test_unlinked_job_falls_back_to_the_ticket(seed: Seed) -> None:
    """Production today: no job is linked to a case yet."""
    job_id = await seed.job()
    await _stack(seed)
    evidence = await _job_case_evidence(_STATE, {"db": seed.db}, _CONFIG, job_id)
    assert evidence is not None
    assert evidence["source"] == "ticket" and evidence["case_id"] is None
    assert evidence["services"] == ["acme-backend"]


async def test_a_service_of_no_stack_of_the_repository_s_is_not_one(seed: Seed) -> None:
    """The deployment allowlists the container, but the repository's stack names others, or there is no stack."""
    job_id = await seed.job()
    unwatched = await _job_case_evidence(_STATE, {"db": seed.db}, _CONFIG, job_id)
    await _stack(seed, containers="acme-ml")
    other = await _job_case_evidence(_STATE, {"db": seed.db}, _CONFIG, job_id)
    assert unwatched is not None and unwatched["services"] == []
    assert other is not None and other["services"] == []


async def test_unlinked_job_without_evidence_has_none(seed: Seed) -> None:
    job_id = await seed.job()
    assert await _job_case_evidence({"task": "Add dark mode"}, {"db": seed.db}, _CONFIG, job_id) is None


async def test_a_broken_pool_never_fails_the_job() -> None:
    evidence = await _job_case_evidence({"task": _TASK}, {"db": object()}, _CONFIG, "job-x")
    assert evidence is not None and evidence["source"] == "ticket"
