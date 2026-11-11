"""Writes addressed to a key's own repository whose effect reaches beyond it, on the real app over a real database.

A route scoped to a repository the key covers stays the key's, except for the
parts of it that are the deployment's:

- a watched stack's LogQL selectors, the containers its analysis may read and
  where it files choose lines from every container the deployment ships to
  Loki and write into a team's tracker: a key with an allowlist changes only
  the rest of a stack an admin made (enabled, thresholds, budget), and makes
  none;
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
            app, "PUT", _STACK, keys.limited, {**same, "enabled": False, "settings": {"job_budget_usd": 2.5}}
        )
        before, refused = await _stored_stack(db), await _refusals(db)
        reaching = {
            "its selector": {**same, "logql": '{container_name=~".+"}'},
            "its deploy selector": {**same, "settings": {"deploy_logql": '{container_name=~".+"}'}},
            "the containers it reads": {**same, "settings": {"context_proxy_container_allowlist": "*"}},
            "its tracker": {**same, "tracker": "github"},
            "its tracker target": {**same, "tracker_target": {"team_id": "team-000000000001"}},
        }
        answers = {name: await call(app, "PUT", _STACK, keys.limited, body) for name, body in reaching.items()}
        after, audited = await _stored_stack(db), await _refusals(db) - refused
        removed = await call(app, "DELETE", _STACK, keys.limited)
        made = await call(app, "PUT", _STACK, keys.limited, same)
        unmade = await db.fetchval("SELECT count(*) FROM watcher_stacks WHERE repo = $1", COVERED)
        opened = await call(app, "PUT", _STACK, keys.open, reaching["its selector"])
    assert kept.status_code == 200, f"enabled and its budget stay the key's: {kept.text}"
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


async def test_keeping_the_shared_build_cache_on_does_not_undo_a_turn_off_made_meanwhile(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key read the setup while it was on; an admin turned it off before the key's save. The save keeps it on
    only over the setup it read, so it answers 409 and the admin's stays."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        assert (await call(app, "PUT", _SETUP_URL, MASTER, _shares(True))).status_code == 200
        read = dict(await app.state.qa_configs_repo.get(COVERED))
        assert (await call(app, "PUT", _SETUP_URL, MASTER, _shares(False))).status_code == 200
        configs = app.state.qa_configs_repo

        async def as_read(repo: str) -> dict[str, Any] | None:
            return read if repo == COVERED else None

        monkeypatch.setattr(configs, "get", as_read)
        saved = await call(app, "PUT", _SETUP_URL, keys.limited, _shares(True, "page loads quickly"))
        left = await db.fetchval("SELECT yaml_text FROM repo_qa_configs WHERE repo = $1", COVERED)
    assert saved.status_code == 409, f"{saved.status_code} {saved.text}"
    assert left == _shares(False), "the admin's turn-off stays"
