"""Writes addressed to a key's own repository whose effect reaches beyond it, on the real app over a real database.

A route scoped to a repository the key covers stays the key's, except for the
parts of it that are the deployment's:

- a watched stack's LogQL selectors, the containers its analysis may read,
  where it files, and its spend, throttle and auto-accept settings choose
  lines from every container the deployment ships to Loki, write into a
  team's tracker and draw on the shared budget: a key with an allowlist
  changes only the rest of a stack an admin made (``enabled`` and the
  repository's settings, what counts as a finding), makes none, and runs no
  organization ``watch`` action, which makes them;
- a test setup's ``cache.turbo_remote`` hands the repository's QA sandboxes
  the Turbo remote-cache token every such repository shares: a key with an
  allowlist keeps it on where an admin turned it on and may turn it off, but
  turns it on by no route, the PUT, applying a setup proposal or undoing a
  change.

Each is refused with the 403 of every deployment-wide write, naming the
deployment, and audited. The app and its keys are
:mod:`tests.fixtures.repository_writes`'.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from embry0.api.repo_allowlist import DEPLOYMENT
from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, MASTER, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

_SETUP = (Path(__file__).parents[2] / "fixtures" / "qa-yaml-corpus" / "v2" / "single-app.yaml").read_text()
_STACK = f"/api/v1/watcher/stacks/{COVERED}"
_SETUP_URL = f"/api/v1/repos/{COVERED}/-/qa-config"


def _shares(on: bool, note: str = "") -> str:
    """The corpus setup with ``cache.turbo_remote.enabled`` ``on``; ``note`` changes something else in it."""
    doc = yaml.safe_load(_SETUP)
    doc.setdefault("cache", {})["turbo_remote"] = {"enabled": on}
    if note:
        doc["defaults"]["acceptance_criteria_template"] = [note]
    return yaml.safe_dump(doc, sort_keys=False)


def _refused(response: Any) -> bool:
    detail = response.json().get("detail", "") if response.status_code == 403 else ""
    return str(detail).startswith("repo_not_allowed") and DEPLOYMENT in str(detail)


async def _refusals(db: DatabasePool) -> int:
    return int(await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'"))


async def _stored_stack(db: DatabasePool) -> dict[str, Any]:
    row = await db.fetchrow(
        "SELECT logql, tracker, tracker_target, settings, enabled FROM watcher_stacks WHERE repo = $1", COVERED
    )
    return dict(row) if row else {}


async def test_a_key_changes_its_watched_stack_but_not_what_it_reads_or_where_it_files(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        stack = (await call(app, "GET", _STACK, keys.limited)).json()
        same = {k: stack[k] for k in ("logql", "tracker", "tracker_target", "settings")}
        kept = await call(
            app,
            "PUT",
            _STACK,
            keys.limited,
            {**same, "enabled": False, "settings": {"spike_factor": 4.0, "proposal_window_days": 3}},
        )
        before, refused = await _stored_stack(db), await _refusals(db)
        reaching = {
            "its selector": {**same, "logql": '{container_name=~".+"}'},
            "its deploy selector": {**same, "settings": {"deploy_logql": '{container_name=~".+"}'}},
            "the containers it reads": {**same, "settings": {"context_proxy_container_allowlist": "*"}},
            "its tracker": {**same, "tracker": "github"},
            "its tracker target": {**same, "tracker_target": {"team_id": "team-000000000001"}},
            "how often it ticks": {**same, "settings": {"interval_seconds": 60}},
            "what an analysis may spend": {**same, "settings": {"job_budget_usd": 50.0}},
            "how many full analyses a day": {**same, "settings": {"max_full_cap_analyses_per_day": 1000}},
            "fix jobs without a person": {**same, "settings": {"auto_accept": True}},
        }
        answers = {name: await call(app, "PUT", _STACK, keys.limited, body) for name, body in reaching.items()}
        after, audited = await _stored_stack(db), await _refusals(db) - refused
        removed = await call(app, "DELETE", _STACK, keys.limited)
        made = await call(app, "PUT", _STACK, keys.limited, same)
        unmade = await db.fetchval("SELECT count(*) FROM watcher_stacks WHERE repo = $1", COVERED)
        opened = await call(app, "PUT", _STACK, keys.open, reaching["its selector"])
    assert kept.status_code == 200, f"enabled and what counts as a finding stay the key's: {kept.text}"
    for name, answer in answers.items():
        assert _refused(answer), f"{name}: {answer.status_code} {answer.text}"
    assert after == before, f"a refused change wrote the stack: {before} -> {after}"
    assert audited == len(reaching), f"each refusal is audited: {audited} rows"
    assert removed.status_code == 200, f"no longer watching its own repository stays the key's: {removed.text}"
    assert _refused(made) and unmade == 0, f"making a stack is the deployment's: {made.status_code} {made.text}"
    assert opened.status_code == 200, f"a key without a list chooses the selector: {opened.text}"


async def test_a_key_never_turns_on_the_shared_build_cache(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not by the PUT; once an admin turned it on, the key keeps it on and turns it off; and once it is off again,
    undoing that turn-off is refused to the key, as is applying a proposal that turns it on."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, values):
        first = await call(app, "PUT", _SETUP_URL, keys.limited, _shares(False))
        refused = await _refusals(db)
        turned_on = await call(app, "PUT", _SETUP_URL, keys.limited, _shares(True))
        still_off = await db.fetchval("SELECT yaml_text FROM repo_qa_configs WHERE repo = $1", COVERED)
        by_admin = await call(app, "PUT", _SETUP_URL, MASTER, _shares(True))
        kept_on = await call(app, "PUT", _SETUP_URL, keys.limited, _shares(True, "page loads quickly"))
        turned_off = await call(app, "PUT", _SETUP_URL, keys.limited, _shares(False))
        undo = await call(
            app, "POST", f"/api/v1/repos/{COVERED}/-/history/{turned_off.json()['revision_id']}/undo", keys.limited
        )
        stored = await db.fetchrow("SELECT yaml_text, updated_at FROM repo_qa_configs WHERE repo = $1", COVERED)
        # The repository's waiting proposal (one at a time), drafted over the setup now stored, turns it on.
        proposal = values[COVERED]["setup_proposal_id"]
        await db.execute(
            "UPDATE repo_setup_proposals SET proposed_yaml = $2, base_updated_at = $3 WHERE id = $1",
            proposal,
            _shares(True),
            stored["updated_at"],
        )
        apply = await call(app, "POST", f"/api/v1/repos/{COVERED}/-/setup/proposal/{proposal}/apply", keys.limited)
        audited = await _refusals(db) - refused
        left = await db.fetchval("SELECT yaml_text FROM repo_qa_configs WHERE repo = $1", COVERED)
        open_undo = await call(
            app, "POST", f"/api/v1/repos/{COVERED}/-/history/{turned_off.json()['revision_id']}/undo", keys.open
        )
    assert first.status_code == 200, first.text
    assert _refused(turned_on) and still_off == _shares(False), (
        f"turning it on: {turned_on.status_code} {turned_on.text}"
    )
    assert by_admin.status_code == 200, by_admin.text
    assert kept_on.status_code == 200, f"kept on where an admin turned it on: {kept_on.text}"
    assert turned_off.status_code == 200, f"turning it off stays the key's: {turned_off.text}"
    assert _refused(undo), f"undoing the turn-off turns it on: {undo.status_code} {undo.text}"
    assert _refused(apply), f"a proposal that turns it on: {apply.status_code} {apply.text}"
    assert left == _shares(False) and audited == 3, f"nothing refused was written ({audited} refusals audited)"
    assert open_undo.status_code == 200, f"a key without a list undoes it: {open_undo.text}"


async def test_keeping_the_shared_build_cache_on_is_checked_against_a_turn_off_made_meanwhile(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An admin is turning it off and has not committed. The key's save keeping it on waits for the setup's row, then
    is checked against the admin's: refused, and the admin's stays."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        assert (await call(app, "PUT", _SETUP_URL, MASTER, _shares(True))).status_code == 200
        async with db.transaction() as conn:
            await conn.execute("UPDATE repo_qa_configs SET yaml_text = $2 WHERE repo = $1", COVERED, _shares(False))
            task = asyncio.create_task(call(app, "PUT", _SETUP_URL, keys.limited, _shares(True, "page loads quickly")))
            await asyncio.sleep(0.5)
            waited = not task.done()
        saved = await task
        left = await db.fetchval("SELECT yaml_text FROM repo_qa_configs WHERE repo = $1", COVERED)
    assert waited, f"the key's save did not wait for the setup being changed: {task.result().status_code}"
    assert _refused(saved), f"checked against the admin's turn-off: {saved.status_code} {saved.text}"
    assert left == _shares(False), "the admin's turn-off stays"


async def test_a_key_s_stack_change_is_checked_against_an_admin_s_made_meanwhile(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An admin is narrowing the stack's selector and has not committed. The key's change of ``enabled``, sending the
    selector it read, waits for the stack's row, then is checked against the admin's: refused, and the admin's
    selector stays."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        stack = (await call(app, "GET", _STACK, keys.limited)).json()
        body = {**{k: stack[k] for k in ("logql", "tracker", "tracker_target", "settings")}, "enabled": False}
        async with db.transaction() as conn:
            await conn.execute("UPDATE watcher_stacks SET logql = '{app=\"web\"}' WHERE repo = $1", COVERED)
            task = asyncio.create_task(call(app, "PUT", _STACK, keys.limited, body))
            await asyncio.sleep(0.5)
            waited = not task.done()
        changed = await task
        left = await _stored_stack(db)
    assert waited, f"the key's change did not wait for the stack being changed: {task.result().status_code}"
    assert _refused(changed), f"checked against the admin's selector: {changed.status_code} {changed.text}"
    assert (left["logql"], left["enabled"]) == ('{app="web"}', True), left


async def test_a_key_runs_no_organization_watch_action(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``watch`` makes stacks with a placeholder selector, so a key could delete its stack an admin narrowed and make it
    again through the organization. Refused to a key with an allowlist, a dry run included, before anything is read;
    a key without a list runs it."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        # The key covers acme as a whole (acme/web is its one registered repository), and discovery saw acme/web.
        await db.execute(
            "INSERT INTO org_repo_facts (owner, repo, default_branch, facts) VALUES ('acme', $1, 'main', '{}'::jsonb) "
            "ON CONFLICT DO NOTHING",
            COVERED,
        )
        removed = await call(app, "DELETE", _STACK, keys.limited)
        refused = await _refusals(db)
        watch = {"action": "watch", "repos": [COVERED]}
        answers = {
            "watch": await call(app, "POST", "/api/v1/orgs/acme/actions", keys.limited, watch),
            "dry run": await call(app, "POST", "/api/v1/orgs/acme/actions", keys.limited, {**watch, "dry_run": True}),
        }
        audited = await _refusals(db) - refused
        unmade = await db.fetchval("SELECT count(*) FROM watcher_stacks WHERE repo = $1", COVERED)
        opened = await call(app, "POST", "/api/v1/orgs/acme/actions", keys.open, watch)
        made = await db.fetchval("SELECT logql FROM watcher_stacks WHERE repo = $1", COVERED)
    assert removed.status_code == 200, removed.text
    for name, answer in answers.items():
        assert _refused(answer), f"{name}: {answer.status_code} {answer.text}"
    assert (audited, unmade) == (2, 0), f"each refusal is audited and no stack is made: {audited} rows, {unmade}"
    assert opened.status_code == 200 and opened.json()["results"][0]["outcome"] == "done", opened.text
    assert made == f'{{repo="{COVERED}"}}', f"the placeholder names the whole repository: {made}"


def test_every_stack_setting_is_the_deployment_s_or_the_repository_s() -> None:
    """A new setting is refused to a key with an allowlist or left to it only once it is placed."""
    from embry0.services.watcher_stacks import DEPLOYMENT_SETTINGS, REPOSITORY_SETTINGS, SETTING_KEYS

    assert DEPLOYMENT_SETTINGS.isdisjoint(REPOSITORY_SETTINGS), sorted(DEPLOYMENT_SETTINGS & REPOSITORY_SETTINGS)
    assert DEPLOYMENT_SETTINGS | REPOSITORY_SETTINGS == SETTING_KEYS, (
        f"unplaced: {sorted(SETTING_KEYS - DEPLOYMENT_SETTINGS - REPOSITORY_SETTINGS)}; "
        f"not settings: {sorted((DEPLOYMENT_SETTINGS | REPOSITORY_SETTINGS) - SETTING_KEYS)}"
    )


async def test_an_onboarding_job_knows_a_key_with_an_allowlist_started_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POST /jobs {"pipeline": "onboard"}``, the organization's onboard action and running an onboarding job again
    tell the job whether a key with an allowlist starts it, whoever started the first one: its draft then leaves the
    shared build cache alone (``test_shared_cache_onboarding.py``)."""
    from unittest.mock import AsyncMock, MagicMock

    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, values):
        executor = MagicMock()
        executor.start_onboard_job = AsyncMock(return_value="job-onboard-000001")
        monkeypatch.setattr(app.state, "issue_executor", executor)
        body = {"pipeline": "onboard", "repo": COVERED, "branch": "main", "skip_smoke": True}
        for token in (keys.limited, keys.open):
            started = await call(app, "POST", "/api/v1/jobs", token, body)
            assert started.status_code == 201, started.text
        await db.execute(
            "INSERT INTO org_repo_facts (owner, repo, default_branch, facts) "
            "VALUES ('acme', $1, 'main', '{\"boot_hint\": \"npm run dev\"}'::jsonb) "
            "ON CONFLICT DO NOTHING",
            COVERED,
        )
        acted = await call(
            app,
            "POST",
            "/api/v1/orgs/acme/actions",
            keys.limited,
            {"action": "onboard", "repos": [COVERED], "force": True},
        )
        started_by = [c.kwargs.get("by_allowlisted_key") for c in executor.start_onboard_job.await_args_list]
        # An admin's finished onboarding job of the key's repository, started again by each key.
        job = values[COVERED]["job_id"]
        launch = {
            "runner": "onboard",
            "repo": COVERED,
            "branch": "main",
            "skip_smoke": True,
            "by_allowlisted_key": False,
        }
        await db.execute(
            "UPDATE jobs SET pipeline_template = 'onboard', launch = $2::jsonb WHERE job_id = $1", job, launch
        )
        again = {}
        for name, token in (("limited", keys.limited), ("open", keys.open)):
            executor.run_again = MagicMock()
            answer = await call(app, "POST", f"/api/v1/jobs/{job}/run-again", token)
            assert answer.status_code == 202, f"{name}: {answer.text}"
            again[name] = executor.run_again.call_args.args[1].get("by_allowlisted_key")
    assert acted.status_code == 200, acted.text
    assert started_by == [True, False, True], f"POST /jobs by each key, then the organization's onboard: {started_by}"
    assert again == {"limited": True, "open": False}, f"running it again takes the caller's: {again}"
