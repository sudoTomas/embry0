"""``POST /jobs`` refuses, in a request's ``pipeline_config``, the keys embry0 sets itself, and bounds its budget.

On the real app over a real database (:mod:`tests.fixtures.repository_writes`), the executor's work stubbed so no job
runs. Each key embry0 sets itself (:data:`SAMPLE`, the keys of ``embry0.api.schemas.INTERNAL_PIPELINE_KEYS``) is a
422 naming it, with no job stored; a
``max_budget_usd`` there is held to the field's bound; other keys pass as before. Running an old job again copies its
stored ``pipeline_config`` as it is, internal keys included, and its body cannot add any.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from embry0.services.issue_executor import IssueExecutor
from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, NOW, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: A value of the shape embry0 writes for each key, so a refusal is the key's and not its value's.
SAMPLE: dict[str, Any] = {
    "areas_proposal_id": "ap-000000000001",
    "proposal_id": "setup-000000000001",
    "run_with": {"model": "claude-sonnet-5"},
    "agent_models_override": {"developer": "claude-sonnet-5"},
    "local_first_model": "custom/local-model",
    "budget_overrun_mode": "soft",
    "open_pr": True,
    "context_fingerprint": "fp-000000000001",
    "head_sha": "0" * 40,
    "actor": "embry0:scout",
    "pipeline": "onboard",
    "branch": "main",
    "skip_smoke": True,
    "qa": {"apps": []},
    "review_id": "rv-000000000001",
    "trigger_key": "pr:acme/web#7",
    "repo": COVERED,
    "forge": "github",
    "number": 7,
    "url": "https://github.com/acme/web/pull/7",
    "title": "A change",
    "body": "",
    "author": "ada",
    "head_ref": "feature",
    "base_ref": "main",
    "from_fork": False,
    "trigger": "manual",
    "depth": "standard",
    "model": "claude-sonnet-5",
}


@pytest.fixture(autouse=True)
def _no_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job the route hands the executor runs nothing here."""

    def track(_self: IssueExecutor, coro: Any, **_kwargs: Any) -> Any:
        coro.close()
        done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        done.set_result(None)
        return done

    monkeypatch.setattr(IssueExecutor, "_track_task", track)


def _job(config: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"repo": COVERED, "task": "Fix the widget", "pipeline_config": config, **fields}


async def _jobs(db: DatabasePool) -> int:
    return int(await db.fetchval("SELECT count(*) FROM jobs"))


async def test_a_request_cannot_set_a_key_embry0_sets_itself(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        before = await _jobs(db)
        answers = {
            key: await call(app, "POST", "/api/v1/jobs", keys.open, _job({key: SAMPLE[key], "notes": "kept"}))
            for key in SAMPLE
        }
        after = await _jobs(db)
    wrong = {
        key: (r.status_code, r.text[:200])
        for key, r in answers.items()
        if r.status_code != 422 or f"pipeline_config cannot set {key}:" not in r.text
    }
    assert wrong == {}, f"keys embry0 sets itself that a request set: {wrong}"
    assert after == before, f"a refused request stored a job: {before} -> {after}"


@pytest.mark.parametrize("budget", [5000, 1000.01, 0, -1, "lots"])
async def test_a_budget_in_pipeline_config_is_held_to_the_field_s_bound(
    budget: Any, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        answer = await call(app, "POST", "/api/v1/jobs", keys.open, _job({"max_budget_usd": budget}))
        field = await call(app, "POST", "/api/v1/jobs", keys.open, {**_job({}), "max_budget_usd": budget})
    assert (answer.status_code, "max_budget_usd" in answer.text) == (422, True), (
        f"pipeline_config.max_budget_usd {budget!r}: {answer.status_code} {answer.text[:200]}"
    )
    assert field.status_code == 422, f"the field refuses it too: {field.status_code}"


async def test_other_keys_and_a_budget_within_the_bound_are_stored(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        made = await call(
            app,
            "POST",
            "/api/v1/jobs",
            keys.limited,
            _job({"max_budget_usd": 12.5, "execution_mode_override": "cli", "notes": "kept"}),
        )
        both = await call(app, "POST", "/api/v1/jobs", keys.open, {**_job({"max_budget_usd": 40}), "max_budget_usd": 9})
    assert made.status_code in (200, 201), made.text
    assert made.json()["pipeline_config"] == {"max_budget_usd": 12.5, "execution_mode_override": "cli", "notes": "kept"}
    assert both.json()["pipeline_config"]["max_budget_usd"] == 9, f"the field wins over the key: {both.text[:200]}"


async def test_an_old_job_runs_again_with_the_keys_embry0_stored_for_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job embry0 started with its own keys, stored before this release, runs again carrying them as stored; the
    run again's body adds none."""
    db = db_with_migrations
    stored = {"open_pr": True, "context_fingerprint": "fp-000000000002", "actor": "embry0:context-audit", "x": 1}
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_config, launch, created_at, finished_at) "
            "VALUES ('job-old-audit', $1, 'Audit', 'completed', $2::jsonb, $3::jsonb, $4, $4)",
            COVERED,
            stored,
            {"runner": "workflow", "additional_context": ""},
            NOW,
        )
        again = await call(app, "POST", "/api/v1/jobs/job-old-audit/run-again", keys.open, {})
        new_id = again.json().get("followup_job_id") if again.status_code == 202 else None
        carried = await db.fetchval("SELECT pipeline_config FROM jobs WHERE job_id = $1", new_id) if new_id else None
        before = await _jobs(db)
        added = await call(
            app,
            "POST",
            "/api/v1/jobs/job-old-audit/run-again",
            keys.open,
            {"pipeline_config": {"open_pr": False, "head_sha": "1" * 40}},
        )
        after = await _jobs(db)
    assert again.status_code == 202, f"the old job runs again: {again.status_code} {again.text[:200]}"
    assert carried == stored, f"what the new job carries: {carried}"
    assert (added.status_code, after) == (422, before), f"a run again's body adds no key: {added.status_code}"
