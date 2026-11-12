"""A repository's areas through the API (spec 2026-11-10): read, write, undo, map, apply and reject.

On the real app over a real database (:mod:`tests.fixtures.repository_writes`): each of its two repositories
has a map of its areas waiting for a person, and no areas yet. Keys are minted per tier; the executor is
stubbed where a map would start a job.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from embry0.api.deps import bust_api_key_cache
from embry0.services.areas_proposals import finish_map
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates
from tests.fixtures.repository_writes import COVERED, MASTER, UNCOVERED, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

AREAS = f"/api/v1/repos/{COVERED}/-/areas"
HISTORY = f"/api/v1/repos/{COVERED}/-/history"
V1 = "# Ours.\nareas:\n  - name: Web\n    paths: [web/]\n    owners: ['@octo']\n"
V2 = "areas:\n  - name: Web\n    paths: [web/**]\n  - name: Api\n    paths: [api/**]\n    owners: [hubot]\n"


@pytest.fixture
async def app(db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, minted, _values):
        app.state.test_limited = minted.limited
        keys = ApiKeysRepository(db_with_migrations)
        _row, app.state.test_operator = await keys.create(
            name="operator", scope="operate", user_id=None, created_by="tests"
        )
        _row, app.state.test_reader = await keys.create(name="reader", scope="read", user_id=None, created_by="tests")
        bust_api_key_cache()
        yield app


async def _put(app: Any, text: str, *, token: str = MASTER, url: str = AREAS, **headers: str) -> Any:
    from httpx import ASGITransport, AsyncClient

    sent = {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}
    sent.update({"Content-Type": "application/yaml", **headers})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.put(url, content=text.encode(), headers=sent)


async def _get(app: Any, url: str = AREAS, token: str = MASTER) -> Any:
    return await call(app, "GET", url, token)


async def test_a_repository_with_no_areas_reads_empty_at_generation_0(app: Any) -> None:
    got = await _get(app)
    assert got.status_code == 200, got.text
    body = got.json()
    assert (body["yaml_text"], body["areas"], body["generation"], body["imported"]) == ("", [], 0, None)
    assert got.headers["etag"] == '"0"'
    assert body["proposal"]["id"] == "areas-widget" and body["proposal"]["status"] == "pending"
    missing = await _get(app, "/api/v1/repos/acme/nobody/-/areas")
    assert (missing.status_code, missing.json()) == (404, {"detail": "acme/nobody is not a registered repository"})


async def test_a_write_is_stored_as_written_read_back_and_recorded(app: Any, db_with_migrations: DatabasePool) -> None:
    put = await _put(app, V1, token=app.state.test_operator)
    assert put.status_code == 200, put.text
    assert (put.json()["generation"], put.json()["changes"][0]["owners_added"]) == (1, ["octo"])
    got = await _get(app)
    body = got.json()
    assert body["yaml_text"] == V1 and got.headers["etag"] == '"1"'
    assert body["areas"] == [{"name": "Web", "slug": "web", "paths": ["web/**"], "logs": [], "owners": ["octo"]}]
    yaml_only = await _get(app, AREAS + "?format=yaml")
    assert (yaml_only.text, yaml_only.headers["content-type"].split(";")[0]) == (V1, "application/yaml")
    again = await _put(app, V1, token=app.state.test_operator)
    assert again.json()["revision_id"] is None, "an unchanged write records nothing"
    rows = await db_with_migrations.fetch(
        "SELECT details FROM audit_log WHERE action = 'repo.areas_set' AND details->>'repo' = $1", COVERED
    )
    assert len(rows) == 1 and rows[0]["details"]["revision_id"] == put.json()["revision_id"]


async def test_every_problem_is_one_422_naming_each_key(app: Any) -> None:
    text = "areas:\n  - name: Web\n    paths: [/web, ok/**]\n    owners: ['no way']\n  - name: web\n  - x: 1\n"
    put = await _put(app, text)
    assert put.status_code == 422, put.text
    detail = put.json()["detail"]
    assert [(".".join(map(str, d["loc"])), d["type"]) for d in detail] == [
        ("areas.0.paths.0", "invalid_glob"),
        ("areas.0.owners.0", "invalid_login"),
        ("areas.2.x", "extra_forbidden"),
        ("areas.2.name", "missing"),
    ]
    assert "no way" not in json.dumps(detail) and "/web" not in json.dumps(detail)
    assert (await _get(app)).json()["generation"] == 0, "nothing is stored"
    syntax = await _put(app, "areas: [")
    assert syntax.status_code == 422 and syntax.json()["detail"][0]["type"] == "yaml_syntax"
    big = await _put(app, "#" * (256 * 1024 + 1))
    assert big.status_code == 413


async def test_if_match_refuses_a_write_over_a_change_made_meanwhile(app: Any) -> None:
    assert (await _put(app, V1, **{"If-Match": '"0"'})).status_code == 200, "0 creates the document"
    stale = await _put(app, V2, **{"If-Match": '"0"'})
    assert stale.status_code == 409 and "changed since you read them" in stale.text
    assert (await _put(app, V2, **{"If-Match": '"1"'})).status_code == 200
    assert (await _put(app, V1, **{"If-Match": "*"})).status_code == 200


async def test_a_dry_run_says_what_would_change_and_stores_nothing(app: Any) -> None:
    await _put(app, V1)
    preview = await _put(app, V2, url=AREAS + "?dry_run=1")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["would_change"] is True and body["revision_id"] is None and body["generation"] == 1
    assert [(c["area"], c["change"]) for c in body["changes"]][0] == ("Api", "added"), "owners come first"
    assert "+  - name: Api" in body["text_diff"]
    assert (await _get(app)).json()["yaml_text"] == V1


async def test_a_json_body_is_stored_as_the_yaml_it_dumps_to(app: Any) -> None:
    put = await call(app, "PUT", AREAS, MASTER, {"areas": [{"name": "Web", "paths": ["web/"]}]})
    assert put.status_code == 200, put.text
    assert (await _get(app)).json()["yaml_text"] == "areas:\n- name: Web\n  paths:\n  - web/**\n"


async def test_a_read_key_reads_and_cannot_write(app: Any) -> None:
    assert (await _get(app, token=app.state.test_reader)).status_code == 200
    refused = await _put(app, V1, token=app.state.test_reader)
    assert refused.status_code == 403, refused.text


async def test_undo_puts_back_the_areas_a_write_replaced(app: Any) -> None:
    await _put(app, V1)
    await _put(app, V2)
    history = (await _get(app, HISTORY)).json()["entries"]
    assert [e["what"] for e in history[:2]] == [
        "Areas changed: Api added; Web changed (owners)",
        "Areas created",
    ]
    undone = await call(app, "POST", f"{HISTORY}/{history[0]['id']}/undo", MASTER)
    assert undone.status_code == 200, undone.text
    assert (await _get(app)).json()["yaml_text"] == V1, "the earlier text, byte for byte"
    newest = (await _get(app, HISTORY)).json()["entries"][0]
    assert (newest["surface"], newest["source"]) == ("areas", "restore")
    again = await call(app, "POST", f"{HISTORY}/{history[0]['id']}/undo", MASTER)
    assert again.status_code == 409, "an undone change cannot be undone twice"


async def test_a_map_waits_for_a_person_and_an_undo_restores_the_areas_before_it(
    app: Any, db_with_migrations: DatabasePool
) -> None:
    """#175's second acceptance criterion: a mapped set applies only after approval, and an undo restores the
    previous set."""
    db = db_with_migrations
    await seed_builtin_pipeline_templates(app.state.templates_repo)
    app.state.issue_executor.submit_job = MagicMock()
    await _put(app, V1)
    started = await call(app, "POST", f"{AREAS}/map", app.state.test_operator, {})
    assert started.status_code == 202, started.text
    proposal_id, job_id = started.json()["proposal_id"], started.json()["job_id"]
    assert (await db.fetchval("SELECT status FROM repo_areas_proposals WHERE id = 'areas-widget'")) == "stale", (
        "the map waiting before is retired by the new one"
    )
    job = await db.fetchrow("SELECT task, pipeline_config FROM jobs WHERE job_id = $1", job_id)
    assert job["pipeline_config"]["areas_proposal_id"] == proposal_id and "open_pr" not in job["pipeline_config"]
    assert "name: Web" in job["task"] and ".embry0/" not in job["task"]
    busy = await call(app, "POST", f"{AREAS}/map", MASTER, {})
    assert busy.status_code == 409 and job_id in busy.text

    status = await finish_map(
        app.state.areas,
        app.state.repo_areas_proposals_repo,
        proposal_id,
        job_id=job_id,
        repo=COVERED,
        document=V2,
        notes="## Changes",
        failure=None,
    )
    assert status == "pending"
    assert (await _get(app)).json()["yaml_text"] == V1, "nothing applies before a person does"
    card = (await _get(app, f"{AREAS}/proposal")).json()
    assert (card["id"], card["status"], card["stale"], card["notes_md"]) == (
        proposal_id,
        "pending",
        False,
        "## Changes",
    )
    assert card["changes"][0] == {
        "area": "Api",
        "change": "added",
        "keys": ["name", "paths", "logs", "owners"],
        "owners_added": ["hubot"],
        "owners_removed": [],
    }
    listed = (await _get(app, "/api/v1/areas/proposals")).json()["proposals"]
    assert [p["id"] for p in listed if p["repo"] == COVERED] == [proposal_id]

    applied = await call(app, "POST", f"{AREAS}/proposal/{proposal_id}/apply", app.state.test_operator)
    assert applied.status_code == 200, applied.text
    assert (await _get(app)).json()["yaml_text"] == V2
    newest = (await _get(app, HISTORY)).json()["entries"][0]
    assert (newest["source"], newest["id"]) == ("proposal", applied.json()["revision_id"])
    twice = await call(app, "POST", f"{AREAS}/proposal/{proposal_id}/apply", MASTER)
    assert (twice.status_code, twice.json()["detail"]) == (409, "These areas were already applied.")

    undone = await call(app, "POST", f"{HISTORY}/{newest['id']}/undo", MASTER)
    assert undone.status_code == 200, undone.text
    assert (await _get(app)).json()["yaml_text"] == V1, "the undo restores the areas the map replaced"
    audit = await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'repo.areas_map_applied'")
    assert audit == 1


async def test_a_map_over_areas_changed_meanwhile_is_stale_and_a_rejected_one_changes_nothing(
    app: Any, db_with_migrations: DatabasePool
) -> None:
    db = db_with_migrations
    await db.execute("UPDATE repo_areas_proposals SET proposed_text = $1 WHERE id = 'areas-widget'", V2)
    await _put(app, V1)
    stale = await call(app, "POST", f"{AREAS}/proposal/areas-widget/apply", MASTER)
    assert stale.status_code == 409 and "changed this repository's areas after the map started" in stale.text
    assert (await _get(app)).json()["yaml_text"] == V1
    assert await db.fetchval("SELECT status FROM repo_areas_proposals WHERE id = 'areas-widget'") == "stale"

    other = f"/api/v1/repos/{UNCOVERED}/-/areas"
    revisions = await db.fetchval("SELECT count(*) FROM repo_config_revisions WHERE repo = $1", UNCOVERED)
    rejected = await call(app, "POST", f"{other}/proposal/areas-gizmo/reject", MASTER)
    assert rejected.status_code == 200, rejected.text
    assert await db.fetchval("SELECT count(*) FROM repo_config_revisions WHERE repo = $1", UNCOVERED) == revisions
    assert (await _get(app, other)).json()["generation"] == 0
    wrong = await call(app, "POST", f"{AREAS}/proposal/areas-gizmo/apply", MASTER)
    assert wrong.status_code == 404, "a map of another repository is not this one's"


# ---- #458's review ---------------------------------------------------------------------------------------------


async def _undo_newest(app: Any) -> Any:
    newest = (await _get(app, HISTORY)).json()["entries"][0]
    return await call(app, "POST", f"{HISTORY}/{newest['id']}/undo", MASTER)


async def test_a_version_read_before_an_undo_never_matches_a_later_write(app: Any) -> None:
    """Undoing the write that created the areas used to drop the generation to 0, so the next write was 1 again
    and an If-Match of the first one overwrote it."""
    first = await _put(app, V1, **{"If-Match": '"0"'})
    assert first.status_code == 200, first.text
    read_then = (await _get(app)).headers["etag"]
    assert (await _undo_newest(app)).status_code == 200
    undone = await _get(app)
    assert undone.json()["areas"] == [] and undone.headers["etag"] != read_then
    assert (await _get(app, HISTORY)).json()["entries"][0]["what"] == "Areas removed"
    assert (await _put(app, V2)).status_code == 200
    stale = await _put(app, "areas:\n  - name: Docs\n    paths: [docs/**]\n", **{"If-Match": read_then})
    assert stale.status_code == 409, f"a version read before the undo overwrote a later write: {stale.status_code}"
    assert (await _get(app)).json()["yaml_text"] == V2


async def test_a_map_started_before_an_undo_is_stale_after_a_later_write(
    app: Any, db_with_migrations: DatabasePool
) -> None:
    db = db_with_migrations
    await _put(app, V1)
    base = int((await _get(app)).json()["generation"])
    await db.execute(
        "UPDATE repo_areas_proposals SET base_generation = $1, proposed_text = $2 WHERE id = 'areas-widget'",
        base,
        "areas:\n  - name: Docs\n    paths: [docs/**]\n",
    )
    assert (await _undo_newest(app)).status_code == 200
    assert (await _put(app, V2)).status_code == 200
    card = (await _get(app, f"{AREAS}/proposal")).json()
    assert card["stale"] is True, "a map started before the undo reads as current over a later write"
    applied = await call(app, "POST", f"{AREAS}/proposal/areas-widget/apply", MASTER)
    assert applied.status_code == 409, f"a stale map applied over a later write: {applied.status_code}"
    assert (await _get(app)).json()["yaml_text"] == V2


async def test_a_map_that_cannot_start_leaves_the_waiting_one_waiting(
    app: Any, db_with_migrations: DatabasePool
) -> None:
    """No map-areas template here: the job cannot start, and the map waiting for a person stays."""
    db = db_with_migrations
    await db.execute("DELETE FROM pipeline_templates WHERE name = 'map-areas'")
    started = await call(app, "POST", f"{AREAS}/map", MASTER, {})
    assert started.status_code == 409, started.text
    rows = await db.fetch("SELECT id, status FROM repo_areas_proposals WHERE repo = $1", COVERED)
    assert [(r["id"], r["status"]) for r in rows] == [("areas-widget", "pending")], (
        f"a map that never started retired the one waiting: {[dict(r) for r in rows]}"
    )


async def test_an_unregistered_repository_s_areas_are_not_put_back(app: Any, db_with_migrations: DatabasePool) -> None:
    await _put(app, V1)
    assert (await _undo_newest(app)).status_code == 200
    await db_with_migrations.execute("DELETE FROM repos WHERE repo = $1", COVERED)
    newest = (await _get(app, HISTORY)).json()["entries"][0]
    assert newest["surface"] == "areas" and newest["undoable"] is False, (
        "History offers to undo a change of areas of a repository that is not registered"
    )
    assert "not registered" in newest["why_not"]
    undone = await call(app, "POST", f"{HISTORY}/{newest['id']}/undo", MASTER)
    assert (undone.status_code, undone.json()) == (404, {"detail": f"{COVERED} is not a registered repository"})


#: Every route that writes a repository's areas, the {repo} it acts on to fill in.
WRITES = [
    ("PUT", "/api/v1/repos/{repo}/-/areas", V1),
    ("POST", "/api/v1/repos/{repo}/-/areas/map", {}),
    ("POST", "/api/v1/repos/{repo}/-/areas/proposal/areas-gizmo/apply", None),
    ("POST", "/api/v1/repos/{repo}/-/areas/proposal/areas-gizmo/reject", None),
    ("POST", "/api/v1/repos/{repo}/-/history/1/undo", None),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITES, ids=[f"{m} {p}" for m, p, _ in WRITES])
async def test_a_write_is_refused_before_anything_is_read(app: Any, method: str, path: str, body: Any) -> None:
    """A key whose list does not cover a repository gets the same answer whether or not it is registered."""
    from tests.fixtures.repository_writes import UNCOVERED as REGISTERED

    answers = {}
    for repo in (REGISTERED, "globex/nosuch"):
        url = path.format(repo=repo)
        if isinstance(body, str):
            response = await _put(app, body, token=app.state.test_limited, url=url)
        else:
            response = await call(app, method, url, app.state.test_limited, body)
        answers[repo] = (response.status_code, response.text.replace(repo, "<repo>"))
    assert answers[REGISTERED] == answers["globex/nosuch"], f"the answers tell the two apart: {answers}"
    assert answers[REGISTERED][0] == 403, answers


async def test_two_first_writes_without_if_match_say_what_happened(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.storage.repositories.repo_areas import AreasChanged

    async def raced(*_args: Any, **_kwargs: Any) -> Any:
        raise AreasChanged(1)

    monkeypatch.setattr(app.state.areas, "save", raced)
    blind = await _put(app, V1)
    assert blind.status_code == 409
    assert "while your change was being saved" in blind.json()["detail"], blind.json()["detail"]
    read = await _put(app, V1, **{"If-Match": '"0"'})
    assert read.status_code == 409 and "changed since you read them" in read.json()["detail"]


async def test_a_put_answers_with_the_generation_it_stored_as_its_etag(app: Any) -> None:
    put = await _put(app, V1)
    assert put.headers.get("etag") == f'"{put.json()["generation"]}"', dict(put.headers)
    again = await _put(app, V2, **{"If-Match": put.headers.get("etag", '"none"')})
    assert again.status_code == 200, "the ETag a PUT answers is the If-Match of the next one"


async def test_apply_and_undo_name_every_problem_as_a_put_does(app: Any, db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    invalid = "areas:\n  - name: Web\n    paths: [/web]\n  - name: web\n"
    await db.execute("UPDATE repo_areas_proposals SET proposed_text = $1 WHERE id = 'areas-widget'", invalid)
    applied = await call(app, "POST", f"{AREAS}/proposal/areas-widget/apply", MASTER)
    put = await _put(app, invalid)
    assert (applied.status_code, put.status_code) == (422, 422)
    shape = [{"loc", "type", "msg"}]
    assert [set(p) for p in applied.json()["detail"]][:1] == shape, applied.json()
    assert [p["loc"] for p in applied.json()["detail"]] == [p["loc"] for p in put.json()["detail"]]

    await _put(app, V1)
    await _put(app, V2)
    await db.execute(
        "UPDATE repo_config_revisions SET before = jsonb_set(before, '{yaml_text}', to_jsonb($1::text)) "
        "WHERE id = (SELECT max(id) FROM repo_config_revisions WHERE repo = $2 AND surface = 'areas')",
        invalid,
        COVERED,
    )
    undone = await _undo_newest(app)
    assert undone.status_code == 422, undone.text
    assert isinstance(undone.json()["detail"], list), f"an undo's 422 is not a list of problems: {undone.json()}"
    assert [p["loc"] for p in undone.json()["detail"]] == [p["loc"] for p in put.json()["detail"]]


async def test_a_deleted_repository_s_waiting_map_is_not_listed(app: Any, db_with_migrations: DatabasePool) -> None:
    listed = (await _get(app, "/api/v1/areas/proposals")).json()["proposals"]
    assert "areas-gizmo" in [p["id"] for p in listed]
    from tests.fixtures.repository_writes import UNCOVERED as GONE

    await db_with_migrations.execute("DELETE FROM repos WHERE repo = $1", GONE)
    listed = (await _get(app, "/api/v1/areas/proposals")).json()["proposals"]
    assert "areas-gizmo" not in [p["id"] for p in listed], "a removed repository's map still waits in the list"


@pytest.mark.parametrize("key", ["areas_proposal_id", "proposal_id", "run_with"])
async def test_a_job_request_cannot_set_what_embry0_sets_for_its_own_work(app: Any, key: str) -> None:
    """A job finishes only its own repository's map: a request naming a map, or any key embry0 writes, is
    refused."""
    created = await call(
        app,
        "POST",
        "/api/v1/jobs",
        app.state.test_limited,
        {"repo": COVERED, "task": "Map it", "pipeline_config": {key: "areas-gizmo"}},
    )
    assert created.status_code == 422, f"pipeline_config.{key} was accepted: {created.status_code}"
    assert key in created.text


# ---- #458's second review ---------------------------------------------------------------------------------------


async def _map_and_finish(app: Any, db: DatabasePool) -> tuple[str, str]:
    """Start a map, let its job end with a document waiting for a person; ``(proposal_id, job_id)``."""
    await seed_builtin_pipeline_templates(app.state.templates_repo)
    app.state.issue_executor.submit_job = MagicMock()
    started = await call(app, "POST", f"{AREAS}/map", MASTER, {})
    assert started.status_code == 202, started.text
    proposal_id, job_id = started.json()["proposal_id"], started.json()["job_id"]
    await db.execute(
        "UPDATE jobs SET status = 'completed', finished_at = NOW(), launch = $2::jsonb WHERE job_id = $1",
        job_id,
        {"runner": "workflow", "issue_id": None, "issue": {"repo": COVERED, "title": "Map"}, "kind": "map_areas"},
    )
    status = await finish_map(
        app.state.areas,
        app.state.repo_areas_proposals_repo,
        proposal_id,
        job_id=job_id,
        repo=COVERED,
        document=V2,
        notes="",
        failure=None,
    )
    assert status == "pending"
    return proposal_id, job_id


async def test_running_a_map_job_again_starts_a_new_map(app: Any, db_with_migrations: DatabasePool) -> None:
    """The review's finding: run again copied the old map's proposal into the new job, which then could not finish
    it, and the old map stayed waiting."""
    db = db_with_migrations
    old_proposal, old_job = await _map_and_finish(app, db)
    again = await call(app, "POST", f"/api/v1/jobs/{old_job}/run-again", MASTER, {})
    assert again.status_code == 202, again.text
    new_job = again.json()["followup_job_id"]
    config = await db.fetchval("SELECT pipeline_config FROM jobs WHERE job_id = $1", new_job)
    new_proposal = config["areas_proposal_id"]
    assert new_proposal != old_proposal, "the job run again delivers to the old map, which it cannot finish"
    rows = {
        r["id"]: (r["status"], r["job_id"])
        for r in await db.fetch("SELECT id, status, job_id FROM repo_areas_proposals WHERE repo = $1", COVERED)
    }
    assert rows[old_proposal][0] == "stale" and rows[new_proposal] == ("running", new_job), rows
    finished = await finish_map(
        app.state.areas,
        app.state.repo_areas_proposals_repo,
        new_proposal,
        job_id=new_job,
        repo=COVERED,
        document=V1,
        notes="",
        failure=None,
    )
    assert finished == "pending", "the new job finishes the new map"


async def test_a_map_whose_job_cannot_be_submitted_holds_nothing(app: Any, db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await seed_builtin_pipeline_templates(app.state.templates_repo)
    app.state.issue_executor.submit_job = MagicMock(side_effect=RuntimeError("the executor is draining"))
    refused = await call(app, "POST", f"{AREAS}/map", MASTER, {})
    assert refused.status_code == 409, refused.text
    jobs = await db.fetch("SELECT status FROM jobs WHERE repo = $1 AND pipeline_template = 'map-areas'", COVERED)
    assert [j["status"] for j in jobs] == ["failed"], f"a job that never started still holds the repository: {jobs}"
    waiting = await db.fetchval("SELECT status FROM repo_areas_proposals WHERE id = 'areas-widget'")
    assert waiting == "pending", "the map waiting before stays waiting"
    app.state.issue_executor.submit_job = MagicMock()
    assert (await call(app, "POST", f"{AREAS}/map", MASTER, {})).status_code == 202, "the next map starts"


async def test_a_map_refused_while_another_starts_never_names_a_finished_job(
    app: Any, db_with_migrations: DatabasePool
) -> None:
    """#458's third review: two starts at once, and the second's 409 named the finished map job, not the one
    starting."""
    db = db_with_migrations
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, finished_at, dispatched_by) "
        "VALUES ('job-done-map', $1, 'Map', 'completed', 'map-areas', NOW(), 'ada')",
        COVERED,
    )
    await db.execute("UPDATE repo_areas_proposals SET status = 'running', job_id = NULL WHERE id = 'areas-widget'")
    refused = await call(app, "POST", f"{AREAS}/map", MASTER, {})
    assert refused.status_code == 409, refused.text
    assert "job-done-map" not in refused.text, f"the 409 names a job that finished: {refused.text}"
    assert "already starting" in refused.json()["detail"]
    await db.execute("UPDATE repo_areas_proposals SET job_id = 'job-running-map' WHERE id = 'areas-widget'")
    named = await call(app, "POST", f"{AREAS}/map", MASTER, {})
    assert "job-running-map" in named.json()["detail"], named.json()


@pytest.mark.parametrize(("bound_to", "carried"), [(COVERED, True), (UNCOVERED, False)])
async def test_running_a_map_job_again_checks_the_account_it_ran_with_for_the_caller(
    app: Any, db_with_migrations: DatabasePool, bound_to: str, carried: bool
) -> None:
    """A map run again with the model and key it ran with checks them again for whoever runs it: an account of the
    caller's own repository is carried, one of a repository it does not cover is not."""
    db = db_with_migrations
    made = await call(
        app,
        "POST",
        "/api/v1/credentials",
        MASTER,
        {"kind": "llm_api_key", "provider": "anthropic", "label": "map-account", "secret": "z" * 24},
    )
    credential = made.json()["id"]
    bound = {"bindings": [{"credential_id": credential, "role": "llm"}]}
    assert (await call(app, "PUT", f"/api/v1/repos/{bound_to}/-/credentials", MASTER, bound)).status_code == 200
    _proposal, old_job = await _map_and_finish(app, db)
    chosen = {"credential_id": credential, "provider": "anthropic", "credential_label": "map-account"}
    await db.execute(
        "UPDATE jobs SET pipeline_config = pipeline_config || jsonb_build_object('run_with', $2::jsonb) "
        "WHERE job_id = $1",
        old_job,
        chosen,
    )
    again = await call(app, "POST", f"/api/v1/jobs/{old_job}/run-again", app.state.test_limited, {})
    assert again.status_code == 202, again.text
    config = await db.fetchval("SELECT pipeline_config FROM jobs WHERE job_id = $1", again.json()["followup_job_id"])
    got = (config.get("run_with") or {}).get("credential_id")
    assert got == (credential if carried else None), f"an account bound to {bound_to}: {config}"
