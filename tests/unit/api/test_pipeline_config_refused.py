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
from tests.fixtures.repository_writes import COVERED, MASTER, NOW, UNCOVERED, call, repository_writes_app

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


@pytest.mark.parametrize("budget", [5000, 1000.01, 0, -1, "lots", "1e3", "100", True])
async def test_a_budget_in_pipeline_config_is_held_to_the_field_s_bound(
    budget: Any, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        answer = await call(app, "POST", "/api/v1/jobs", keys.open, _job({"max_budget_usd": budget}))
        field = await call(app, "POST", "/api/v1/jobs", keys.open, {**_job({}), "max_budget_usd": budget})
    assert (answer.status_code, "max_budget_usd" in answer.text) == (422, True), (
        f"pipeline_config.max_budget_usd {budget!r}: {answer.status_code} {answer.text[:200]}"
    )
    if not isinstance(budget, str | bool):
        # A number past the bound is refused by the field too; the field reads a string or a boolean as a number,
        # which pipeline_config does not.
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


async def _old_job(db: DatabasePool, job_id: str, config: dict[str, Any], **columns: Any) -> None:
    """A finished job of the covered repository, stored as an earlier release stored it, with a launch record."""
    launch = columns.pop("launch", {"runner": "workflow", "additional_context": ""})
    names = ", ".join(columns)
    marks = "".join(f", ${n}" for n in range(6, 6 + len(columns)))
    await db.execute(
        f"INSERT INTO jobs (job_id, repo, task, status, pipeline_config, launch, created_at, finished_at"
        f"{', ' + names if columns else ''}) VALUES ($1, $2, 'Run it', 'completed', $3::jsonb, $4::jsonb, $5, $5{marks})",
        job_id,
        COVERED,
        config,
        launch,
        NOW,
        *columns.values(),
    )


async def _run_again(app: Any, db: DatabasePool, token: str, job_id: str) -> tuple[Any, dict[str, Any] | None, str]:
    """Run ``job_id`` again through ``token``: the answer, the new job's stored config and the new job's message."""
    said: list[str] = []
    real = IssueExecutor.run_again

    def recorded(self: IssueExecutor, new_id: str, launch: Any, *, issue_id: Any, message: str) -> Any:
        said.append(message)
        return real(self, new_id, launch, issue_id=issue_id, message=message)

    IssueExecutor.run_again = recorded  # type: ignore[method-assign]
    try:
        answer = await call(app, "POST", f"/api/v1/jobs/{job_id}/run-again", token, {})
    finally:
        IssueExecutor.run_again = real  # type: ignore[method-assign]
    new_id = answer.json().get("followup_job_id") if answer.status_code == 202 else None
    carried = await db.fetchval("SELECT pipeline_config FROM jobs WHERE job_id = $1", new_id) if new_id else None
    return answer, carried, " ".join(said)


async def test_an_old_job_of_embry0_s_own_flow_runs_again_with_the_keys_embry0_stored_for_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A context audit embry0 started, stored with its own keys, runs again carrying them; the run again's body adds
    none."""
    db = db_with_migrations
    stored = {"open_pr": True, "context_fingerprint": "fp-000000000002", "actor": "embry0:context-audit", "x": 1}
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _old_job(
            db,
            "job-old-audit",
            stored,
            dispatched_by="embry0:context-audit",
            pipeline_template="context-audit",
        )
        again, carried, _said = await _run_again(app, db, keys.open, "job-old-audit")
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


async def test_a_job_a_request_started_runs_again_without_embry0_s_keys(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job a request stored with embry0's own keys, before they were refused, runs again without them, its budget
    held to the bound, and the new job says what it did not carry."""
    db = db_with_migrations
    stored = {
        "open_pr": True,
        "actor": "embry0:scout",
        "context_fingerprint": "fp-000000000003",
        "max_budget_usd": 100000,
        "execution_mode_override": "cli",
        "notes": "kept",
    }
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _old_job(db, "job-old-client", stored, dispatched_by="key:limited")
        await _old_job(db, "job-old-words", {"max_budget_usd": "lots"}, dispatched_by="key:limited")
        again, carried, said = await _run_again(app, db, keys.limited, "job-old-client")
        _w, words, _ws = await _run_again(app, db, keys.limited, "job-old-words")
    assert again.status_code == 202, again.text
    assert carried == {"max_budget_usd": 1000.0, "execution_mode_override": "cli", "notes": "kept"}, carried
    for key in ("open_pr", "actor", "context_fingerprint"):
        assert f"{key} is not carried" in said, f"the new job says {key} was not carried: {said}"
    assert "max_budget_usd is held to 1000" in said, said
    assert words == {}, f"a budget that is no number is not carried: {words}"


async def test_a_run_again_checks_the_account_it_ran_with_against_the_caller(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The account an old job ran with is checked again for whoever runs it again: one it may use is carried, one it
    may not is dropped and the new job runs with the deployment's choice, saying so."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        made = {}
        for label, repo in (("widgets-account", COVERED), ("gizmos-account", UNCOVERED)):
            created = await call(
                app,
                "POST",
                "/api/v1/credentials",
                MASTER,
                {"kind": "llm_api_key", "provider": "anthropic", "label": label, "secret": "z" * 24},
            )
            made[label] = created.json()["id"]
            bound = await call(
                app,
                "PUT",
                f"/api/v1/repos/{repo}/-/credentials",
                MASTER,
                {"bindings": [{"credential_id": made[label], "role": "llm"}]},
            )
            assert bound.status_code == 200, bound.text
        for label in made:
            chosen = {"credential_id": made[label], "provider": "anthropic", "credential_label": label}
            await _old_job(db, f"job-ran-with-{label}", {"run_with": chosen}, dispatched_by="key:limited")
        own = await _run_again(app, db, keys.limited, "job-ran-with-widgets-account")
        other = await _run_again(app, db, keys.limited, "job-ran-with-gizmos-account")
    assert (
        own[0].status_code == 202 and (own[1] or {}).get("run_with", {}).get("credential_id") == made["widgets-account"]
    ), f"its own repository's account is carried: {own[1]}"
    assert other[0].status_code == 202 and "run_with" not in (other[1] or {}), (
        f"an account of a repository it does not cover is dropped: {other[1]}"
    )
    assert "is not one this run may use" in other[2], f"the new job says so: {other[2]}"


async def test_a_runner_keeps_the_keys_it_reads(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A QA job runs again with its pipeline, branch and test setup; an area map's hard budget is carried, a soft one
    is not, and an area map writes no open_pr."""
    db = db_with_migrations
    qa = {"pipeline": "qa", "branch": "main", "qa": {"apps": []}}
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _old_job(
            db,
            "job-old-qa",
            qa,
            dispatched_by="key:open",
            launch={"runner": "qa", "repo": COVERED, "branch": "main", "qa_overrides": {}},
        )
        await _old_job(
            db, "job-old-map", {"open_pr": True, "budget_overrun_mode": "hard"}, pipeline_template="map-areas"
        )
        await _old_job(
            db, "job-old-map-soft", {"open_pr": True, "budget_overrun_mode": "soft"}, pipeline_template="map-areas"
        )
        ran = {
            name: await _run_again(app, db, keys.open, name)
            for name in ("job-old-qa", "job-old-map", "job-old-map-soft")
        }
    assert ran["job-old-qa"][1] == qa, ran["job-old-qa"]
    assert ran["job-old-map"][1] == {"budget_overrun_mode": "hard"}, ran["job-old-map"]
    assert ran["job-old-map-soft"][1] == {}, ran["job-old-map-soft"]
