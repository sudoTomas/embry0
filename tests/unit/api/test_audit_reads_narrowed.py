"""``GET /audit`` shows a key with a repository allowlist only the rows about the repositories it covers.

On the real app over a real database (:mod:`tests.fixtures.repository_writes`),
the limited key is admin scope and covers ``acme/web``; ``globex/gizmos`` is a
repository it does not cover, each with a job, an issue, a merge approval and
a hand-off. A row is about the repositories its action names it by
(:mod:`embry0.audit.subjects`): its details' repository, its issue's, its
job's, its approval's, its hand-off's, a repository's settings layer or
credential binding. The key reads a row about repositories when it covers
every one the row names, whoever wrote it, and no row about none (an owner's
or the deployment's), whoever wrote it: an actor string is not one key's, so
its own actor opens nothing. A refusal's row it never reads. The list is
narrowed in its query, so its pages and its ``total`` hold only those rows,
and the Audit filter offers it only the actions of those rows.

A key without a list, and the master key, read every row as before.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from fastapi import FastAPI

from embry0.api.deps import bust_api_key_cache
from embry0.audit.actions import AUDIT_ACTION_LABELS, AUDIT_ACTIONS, RETIRED_AUDIT_ACTIONS
from embry0.orchestration.dispatch_actors import MASTER_KEY, REPO_ALLOWLIST
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_writes import COVERED, OWNER, UNCOVERED, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: What every row written here about a repository the limited key does not cover carries.
HIDDEN_MARK = "hidden-row-mark"
LIMITED, OPEN = "key:limited", "key:open"


async def _items(db: DatabasePool, repo: str) -> dict[str, str]:
    """``repo``'s seeded job, issue, merge approval and hand-off."""
    return {
        "job_id": str(await db.fetchval("SELECT job_id FROM jobs WHERE repo = $1 ORDER BY job_id LIMIT 1", repo)),
        "issue_id": str(await db.fetchval("SELECT id FROM issues WHERE repo = $1 ORDER BY id LIMIT 1", repo)),
        "approval_id": str(await db.fetchval("SELECT id FROM merge_approvals WHERE repo = $1 LIMIT 1", repo)),
        "handoff_id": str(await db.fetchval("SELECT id FROM handoffs WHERE repo = $1 LIMIT 1", repo)),
    }


async def _row(
    db: DatabasePool, action: str, actor: str, details: Mapping[str, Any], issue_id: str | None = None
) -> int:
    return int(
        await db.fetchval(
            "INSERT INTO audit_log (action, actor, details, issue_id) VALUES ($1, $2, $3, $4) RETURNING id",
            action,
            actor,
            dict(details),
            issue_id,
        )
    )


def _everywhere(repo: str, items: Mapping[str, str], mark: str) -> dict[str, Any]:
    """Details naming ``repo``, its owner and its items in every place a row can name them."""
    owner = OWNER[repo]
    return {
        "repo": repo,
        "scope": f"repository:{repo}",
        "scope_kind": "repo",
        "scope_value": repo,
        "owner": owner,
        "before": owner,
        "after": owner,
        "job_id": items["job_id"],
        "followup_job_id": items["job_id"],
        "issue_id": items["issue_id"],
        "approval_id": items["approval_id"],
        "handoff_id": items["handoff_id"],
        "mark": mark,
    }


async def _read_all(app: FastAPI, token: str, query: str = "") -> tuple[list[dict[str, Any]], int]:
    """Every row ``GET /audit`` shows ``token``'s caller, page by page, and the ``total`` it reports."""
    rows: list[dict[str, Any]] = []
    total = -1
    while True:
        response = await call(app, "GET", f"/api/v1/audit?limit=200&offset={len(rows)}{query}", token)
        assert response.status_code == 200, response.text
        body = response.json()
        total = int(body["total"])
        rows += body["entries"]
        if not body["entries"]:
            return rows, total


async def _ids(app: FastAPI, token: str, query: str = "") -> set[int]:
    rows, total = await _read_all(app, token, query)
    assert total == len(rows), f"total {total} is not the {len(rows)} rows the pages hold"
    return {int(r["id"]) for r in rows}


async def test_a_row_naming_only_another_repository_is_never_read_whatever_its_action(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every registered action, and every retired name, with a row naming the other repository, its owner and its
    items in every place a row can: none is read by the limited key, which reads them all without a list."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        items = await _items(db, UNCOVERED)
        details = _everywhere(UNCOVERED, items, HIDDEN_MARK)
        written = {
            await _row(db, action, OPEN, details, items["issue_id"]): action
            for action in sorted({*AUDIT_ACTIONS, *RETIRED_AUDIT_ACTIONS})
        }
        limited = await _ids(app, keys.limited)
        everyone = await _ids(app, keys.open)
    read = sorted(written[i] for i in limited & set(written))
    assert read == [], f"the limited key read rows about {UNCOVERED} only: {read}"
    assert set(written) <= everyone, "a key without a list does not read every row"


async def test_a_row_of_its_own_about_another_repository_or_owner_is_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key's own actor opens no row about another repository, an owner or the deployment, whatever the owner it
    covers and whatever the action: such rows come from a session of the person a key is minted for, or from another
    key of its name, recorded under the same actor."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        items = await _items(db, UNCOVERED)
        own = {
            await _row(db, "repo.preferences_set", LIMITED, {"repo": UNCOVERED, "fields": ["notes"]}): "a repository",
            await _row(db, "issue.created", LIMITED, {"issue_id": items["issue_id"]}, items["issue_id"]): "an issue",
            await _row(db, "job.review.evaluated", LIMITED, {"job_id": items["job_id"]}): "a job",
            await _row(db, "merge.decided", LIMITED, {"approval_id": items["approval_id"]}): "an approval",
            await _row(db, "handoff.created", LIMITED, {"handoff_id": items["handoff_id"]}): "a hand-off",
            await _row(db, "settings.updated", LIMITED, {"scope": f"repository:{UNCOVERED}"}): "a settings layer",
            await _row(
                db, "credential.bound", LIMITED, {"scope_kind": "repo", "scope_value": UNCOVERED}
            ): "a repository's binding",
            await _row(db, "organization.patch", LIMITED, {"owner": OWNER[UNCOVERED]}): "another owner",
            await _row(db, "organization.patch", LIMITED, {"owner": OWNER[COVERED]}): "its own repository's owner",
            await _row(db, "settings.updated", LIMITED, {"scope": f"organization:{OWNER[UNCOVERED]}"}): "its layer",
            await _row(
                db, "credential.bound", LIMITED, {"scope_kind": "owner", "scope_value": f"github:{OWNER[UNCOVERED]}"}
            ): "an owner's binding",
            await _row(db, "map.owner_changed", LIMITED, {"before": OWNER[UNCOVERED], "after": None}): "a map",
            # The deployment's changes such a key cannot make: a session of the person it is minted for made them.
            await _row(db, "apikey.created", LIMITED, {"name": "bot", "allowed_repos": [UNCOVERED]}): "a key minted",
            await _row(db, "credential.created", LIMITED, {"credential_id": "cred-000000000002"}): "a credential",
            await _row(db, "host.created", LIMITED, {"host_id": "host-000000000009"}): "a host",
            await _row(db, "settings.updated", LIMITED, {"scope": "deployment"}): "the deployment's settings",
            await _row(db, "user.login", LIMITED, {"tier": "admin"}): "a sign-in",
            await _row(
                db, "credential.updated", LIMITED, {"credential_id": "cred-000000000003"}
            ): "a credential renamed",
            await _row(
                db, "credential.rotated", LIMITED, {"credential_id": "cred-000000000003"}
            ): "a credential rotated",
            await _row(db, "apikey.revoked", LIMITED, {"key_id": "key-000000000004", "name": "x"}): "a key revoked",
        }
        limited = await _ids(app, keys.limited)
    read = sorted(own[i] for i in limited & set(own))
    assert read == [], f"the limited key read its own rows about what it does not cover: {read}"


async def test_a_row_naming_its_repository_and_another_is_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Work crosses repositories when an issue moves after its job ran, or a follow-up runs where the pull request
    is: a row naming the key's repository and another one is the other one's too."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        mine, theirs = await _items(db, COVERED), await _items(db, UNCOVERED)
        mixed = {
            await _row(
                db, "job.review.sent_back", OPEN, {"job_id": mine["job_id"], "followup_job_id": theirs["job_id"]}
            ): "a follow-up elsewhere",
            await _row(
                db,
                "case_accepted",
                OPEN,
                {"repo": COVERED, "issue_id": theirs["issue_id"], "job_id": mine["job_id"]},
            ): "a retry on an issue that moved",
            await _row(
                db, "issue.job_created", OPEN, {"job_id": theirs["job_id"]}, mine["issue_id"]
            ): "an issue's job elsewhere",
            await _row(db, "job_created", OPEN, {"repo": COVERED, "job_id": theirs["job_id"]}): "a job elsewhere",
        }
        limited = await _ids(app, keys.limited)
    read = sorted(mixed[i] for i in limited & set(mixed))
    assert read == [], f"the limited key read rows naming {UNCOVERED} beside {COVERED}: {read}"


async def test_the_rows_about_its_repository_are_read_whoever_wrote_them_and_the_deployment_s_are_not(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        mine = await _items(db, COVERED)
        about = {
            await _row(db, "repo.preferences_set", OPEN, {"repo": COVERED, "fields": ["notes"]}): "its registration",
            await _row(db, "issue.created", OPEN, {"issue_id": mine["issue_id"]}, mine["issue_id"]): "its issue",
            await _row(db, "job.review.evaluated", OPEN, {"job_id": mine["job_id"]}): "its job",
            await _row(db, "quest.evaluated", OPEN, {"job_id": mine["job_id"]}): "its job, under a retired name",
            await _row(db, "merge.decided", OPEN, {"approval_id": mine["approval_id"]}): "its approval",
            await _row(db, "handoff.created", OPEN, {"handoff_id": mine["handoff_id"]}): "its hand-off",
            await _row(db, "settings.updated", OPEN, {"scope": f"repository:{COVERED}"}): "its settings layer",
            await _row(db, "credential.bound", OPEN, {"scope_kind": "repo", "scope_value": COVERED}): "its binding",
            await _row(db, "repo.preferences_set", OPEN, {"repo": COVERED.upper()}): "its slug in another case",
            await _row(db, "job.review.evaluated", MASTER_KEY, {"job_id": mine["job_id"]}): "the master key's",
        }
        deployment = {
            await _row(db, "credential.created", OPEN, {"credential_id": "cred-000000000001"}): "a credential",
            await _row(db, "apikey.created", OPEN, {"name": "other", "allowed_repos": None}): "a key",
            await _row(db, "host.created", OPEN, {"host_id": "host-000000000009"}): "a host",
            await _row(db, "settings.updated", OPEN, {"scope": "deployment"}): "the deployment's settings",
            await _row(db, "settings.updated", OPEN, {"scope": f"organization:{OWNER[COVERED]}"}): "its owner's layer",
            await _row(db, "organization.patch", OPEN, {"owner": OWNER[COVERED]}): "its owner",
            await _row(db, "context_config_updated", OPEN, {"scope": "global"}): "the global context",
            await _row(db, "credential.bound", OPEN, {"scope_kind": "global", "scope_value": ""}): "every repository",
            await _row(db, "job.review.evaluated", OPEN, {"job_id": "job-gone"}): "a job that is not there",
        }
        limited = await _ids(app, keys.limited)
    unread = sorted(about[i] for i in set(about) - limited)
    assert unread == [], f"rows about {COVERED} the limited key does not read: {unread}"
    read = sorted(deployment[i] for i in limited & set(deployment))
    assert read == [], f"rows of the deployment, not the key's own, that the limited key reads: {read}"


async def _deployment_activity(app: FastAPI, token: str, label: str) -> str:
    """Through ``token``, a key without a list: the host's credential made, bound, rotated and changed, and a key of
    another repository revoked. The credential's id."""
    made = await call(
        app,
        "POST",
        "/api/v1/credentials",
        token,
        {"kind": "forge_token", "provider": "github", "label": label, "secret": "y" * 24},
    )
    assert made.status_code == 201, made.text
    credential = str(made.json()["id"])
    for method, url, body in (
        (
            "PUT",
            "/api/v1/credentials/bindings",
            {
                "scope_kind": "host",
                "scope_value": "github.com",
                "bindings": [{"credential_id": credential, "role": "forge"}],
            },
        ),
        ("POST", f"/api/v1/credentials/{credential}/rotate", {"secret": "z" * 24}),
        ("PUT", f"/api/v1/credentials/{credential}", {"label": f"{label}-renamed", "status": "disabled"}),
    ):
        done = await call(app, method, url, token, body)
        assert done.status_code == 200, f"{method} {url}: {done.text}"
    outside, _secret = await ApiKeysRepository(app.state.db).create(
        name="outside-ci", scope="admin", user_id=None, created_by="tests", allowed_repos=[UNCOVERED]
    )
    revoked = await call(app, "DELETE", f"/api/v1/apikeys/{outside.id}", token)
    assert revoked.status_code in (200, 204), revoked.text
    return credential


def _deployment_rows(rows: list[dict[str, Any]], credential: str) -> list[tuple[str, str]]:
    return [
        (r["action"], r["actor"])
        for r in rows
        if r["details"].get("credential_id") == credential or r["details"].get("name") == "outside-ci"
    ]


@pytest.mark.parametrize("shared", ["a person's keys", "two keys of one name"])
async def test_a_key_does_not_read_what_another_caller_wrote_under_the_same_actor(
    shared: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An actor string is not one key's: the keys a person mints from a session are recorded under the person's email,
    and keys of one name under ``key:<name>``. A key with a list reads none of the deployment's rows another key of
    its actor wrote: the host's credential made, bound, rotated and changed, a key of another repository revoked."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, _keys, _values):
        keys = ApiKeysRepository(db)
        if shared == "a person's keys":
            await db.execute(
                "INSERT INTO users (id, auth0_sub, email, name, tier) "
                "VALUES ('usr-ada', 'auth0|ada', 'ada@example.com', 'ada', 'admin')"
            )
            owner: str | None = "usr-ada"
            names = ("ada-open", "ada-bot")
        else:
            owner, names = None, ("bot", "bot")
        _row_open, unrestricted = await keys.create(name=names[0], scope="admin", user_id=owner, created_by="tests")
        _row_bot, restricted = await keys.create(
            name=names[1], scope="admin", user_id=owner, created_by="tests", allowed_repos=[COVERED]
        )
        bust_api_key_cache()
        credential = await _deployment_activity(app, unrestricted, "host-forge")
        everyone, _ = await _read_all(app, unrestricted)
        read, _ = await _read_all(app, restricted)
    written = _deployment_rows(everyone, credential)
    assert len(written) >= 5, f"the unrestricted key's rows were not written: {written}"
    assert len({actor for _action, actor in written}) == 1, written
    assert _deployment_rows(read, credential) == [], (
        f"{shared}: the key with a list read the deployment's rows another key of its actor wrote: "
        f"{_deployment_rows(read, credential)}"
    )


async def test_the_list_is_narrowed_before_it_pages(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many newer rows about another repository do not push the key's row off its first page, nor count."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        before = await _ids(app, keys.limited)
        mine = await _row(db, "repo.preferences_set", OPEN, {"repo": COVERED, "fields": ["notes"]})
        for _ in range(30):
            await _row(db, "repo.preferences_set", OPEN, {"repo": UNCOVERED, "fields": ["notes"], "m": HIDDEN_MARK})
        first = await call(app, "GET", "/api/v1/audit?limit=1", keys.limited)
        pages = [
            (await call(app, "GET", f"/api/v1/audit?limit=2&offset={offset}", keys.limited)).json()["entries"]
            for offset in range(0, len(before) + 4, 2)
        ]
    assert first.status_code == 200, first.text
    assert [e["id"] for e in first.json()["entries"]] == [mine], f"the key's newest row is not first: {first.text}"
    assert first.json()["total"] == len(before) + 1, f"total counts rows the key does not read: {first.text}"
    paged = [e["id"] for page in pages for e in page]
    assert sorted(paged) == sorted(before | {mine}), "paging two at a time does not give every row the key reads"
    assert HIDDEN_MARK not in json.dumps(pages), "a page holds a row about another repository"


async def test_a_refusal_s_row_is_never_read_and_the_actor_filter_stays_within_the_rows_it_reads(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        refused = await call(app, "PUT", f"/api/v1/repos/{UNCOVERED}/-/preferences", keys.limited, {"notes": "x"})
        assert refused.status_code == 403, refused.text
        await _row(db, "repo.preferences_set", OPEN, {"repo": COVERED, "fields": ["notes"]})
        await _row(db, "repo.preferences_set", OPEN, {"repo": UNCOVERED, "fields": ["notes"], "m": HIDDEN_MARK})
        rows, _total = await _read_all(app, keys.limited)
        by_open, _ = await _read_all(app, keys.limited, f"&actor={OPEN}")
        refusals, _ = await _read_all(app, keys.limited, "&action=apikey.repo_refused")
    assert all(r["actor"] != REPO_ALLOWLIST for r in rows), "the limited key reads a refusal's row"
    assert refusals == [], f"the action filter finds a refusal's row: {refusals}"
    assert by_open and all(r["actor"] == OPEN for r in by_open), by_open
    assert HIDDEN_MARK not in json.dumps(by_open), "the actor filter finds a row about another repository"


async def test_an_operate_key_with_an_allowlist_reads_only_its_own_rows_about_its_repositories(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Below admin, a caller reads only its own rows; with an allowlist, only those about its repositories."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, _keys, _values):
        _row_op, operate = await ApiKeysRepository(db).create(
            name="dispatch", scope="operate", user_id=None, created_by="tests", allowed_repos=[COVERED]
        )
        bust_api_key_cache()
        mine = await _row(db, "repo.preferences_set", "key:dispatch", {"repo": COVERED})
        elsewhere = await _row(db, "repo.preferences_set", "key:dispatch", {"repo": UNCOVERED})
        others = await _row(db, "repo.preferences_set", OPEN, {"repo": COVERED})
        read = await _ids(app, operate)
    assert read == {mine}, f"an operate key with an allowlist reads {read}, not only {mine}"
    assert elsewhere not in read and others not in read


async def test_the_actions_offered_are_those_of_the_rows_the_key_reads(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Audit filter's list (``GET /audit/actions``) names, to the limited key, only actions of rows it reads: a
    current action whose rows, under it or a name it replaced, it reads, and a retired name some such row carries.
    A key without a list gets the whole registry."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        mine, theirs = await _items(db, COVERED), await _items(db, UNCOVERED)
        await _row(db, "quest.evaluated", OPEN, {"job_id": mine["job_id"]})
        await _row(db, "quest.rejected", OPEN, {"job_id": theirs["job_id"]})
        await _row(db, "host.created", OPEN, {"host_id": "host-000000000009"})
        await _row(db, "merge.decided", OPEN, {"approval_id": theirs["approval_id"]})
        rows, _total = await _read_all(app, keys.limited)
        limited = await call(app, "GET", "/api/v1/audit/actions", keys.limited)
        everyone = await call(app, "GET", "/api/v1/audit/actions", keys.open)
    assert limited.status_code == 200 and everyone.status_code == 200, (limited.text, everyone.text)
    offered = {entry["action"] for entry in limited.json()["actions"]}
    carried = {r["action"] for r in rows}
    expected = {RETIRED_AUDIT_ACTIONS.get(a, a) for a in carried} | (carried & set(RETIRED_AUDIT_ACTIONS))
    assert offered == expected, f"offered {sorted(offered)}, its rows carry {sorted(carried)}"
    assert {"job.review.evaluated", "quest.evaluated"} <= offered, offered
    assert not {"quest.rejected", "host.created", "merge.decided"} & offered, offered
    assert [e["action"] for e in everyone.json()["actions"]] == [
        *AUDIT_ACTION_LABELS,
        *RETIRED_AUDIT_ACTIONS,
    ], "a key without a list is not offered the whole registry"
