"""What names a job of a repository a key's allowlist does not cover names no job id of it.

On the real app over a real database (:mod:`tests.fixtures.job_reads`). A
list of things that are each a job's, the merge approvals, the watcher's
ticks and a knowledge map's work, holds only those of the repositories the
key covers, narrowed before its limit. A view of a repository that names a
job, its test setup proposal, its checklist, its place under a standard, an
owner's context audits, names no job of a repository the key does not cover:
since the repository reads, the view reads as one of a repository, or an
owner, nobody registered.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from embry0.api.deps import bust_api_key_cache
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from embry0.storage.repositories.users import UsersRepository
from tests.fixtures.job_reads import JOB, audit_job, job_reads_app, same_answer
from tests.fixtures.repository_reads import same_answer as same_repository_answer
from tests.fixtures.repository_writes import COVERED, NOW, OWNER, UNCOVERED, call, ids
from tests.unit.api.test_job_reads_inventory import MISSING_APPROVAL

pytestmark = pytest.mark.requires_postgres

#: How many newer items the uncovered repository has: more than a page holds.
NEWER = 60


async def test_the_merge_approvals_list_holds_the_key_s_before_its_limit(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with job_reads_app(db, monkeypatch) as (app, keys, _values):
        for i in range(NEWER):
            await db.execute(
                "INSERT INTO merge_approvals (id, repo, pr_number, pr_url, head_sha, job_id, status, created_at) "
                "VALUES ($1, $2, $3, $4, 'abc', $5, 'pending', $6)",
                f"ma-newer-{i:03d}",
                UNCOVERED,
                200 + i,
                f"https://github.com/{UNCOVERED}/pull/{200 + i}",
                JOB[UNCOVERED],
                NOW + timedelta(seconds=i),
            )
        limited = (await call(app, "GET", "/api/v1/merge-approvals?limit=1", keys.limited)).json()
        everyone = (await call(app, "GET", "/api/v1/merge-approvals?limit=1", keys.open)).json()
    assert [r["repo"] for r in everyone["items"]] == [UNCOVERED], "the newer approvals fill the page"
    assert [(r["id"], r["job_id"]) for r in limited["items"]] == [(ids(COVERED)["approval_id"], JOB[COVERED])], (
        f"the limited key's page holds its own repository's approval: {limited}"
    )


async def test_holding_another_repository_s_hand_off_decides_no_merge_for_a_key_that_does_not_cover_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person may decide the merge of a quest handed to them, admin or not. Through a key whose list does not
    cover the quest's repository the hand-off counts for nothing: the decision answers as for an approval that does
    not exist, which a key that is no admin is refused."""
    db = db_with_migrations
    async with job_reads_app(db, monkeypatch) as (app, _keys, _values):
        user = await UsersRepository(db).upsert_login(
            auth0_sub="auth0|ada", email="ada@example.com", name="Ada", tier="operator"
        )
        _row, held = await ApiKeysRepository(db).create(
            name="ada-bot", scope="operate", user_id=user.id, created_by="tests", allowed_repos=[COVERED]
        )
        bust_api_key_cache()
        body = {"decision": "reject"}
        uncovered = await call(
            app, "POST", f"/api/v1/merge-approvals/{ids(UNCOVERED)['approval_id']}/decide", held, body
        )
        missing = await call(app, "POST", f"/api/v1/merge-approvals/{MISSING_APPROVAL}/decide", held, body)
        status = await db.fetchval("SELECT status FROM merge_approvals WHERE id = $1", ids(UNCOVERED)["approval_id"])
    held_off = await db.fetchval("SELECT to_email FROM handoffs WHERE decision_id = $1", f"done:{JOB[UNCOVERED]}")
    assert held_off == "ada@example.com", "the quest is handed to the key's person"
    differ = same_answer(uncovered, ids(UNCOVERED)["approval_id"], missing, MISSING_APPROVAL)
    assert differ is None and missing.status_code == 403, (
        f"the hand-off of another repository's quest decides nothing for the key, as for a missing approval: {differ}"
    )
    assert status == "pending", f"the approval is left as it was: {status}"


async def test_the_watcher_s_ticks_are_the_key_s_stacks_before_their_limit(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with job_reads_app(db, monkeypatch) as (app, keys, _values):
        for i in range(NEWER):
            await db.execute(
                "INSERT INTO watcher_runs (id, window_start, window_end, action, job_id, repo, created_at) "
                "VALUES ($1, $2, $2, 'analysed', $3, $4, $5)",
                f"wr-newer-{i:03d}",
                NOW,
                JOB[UNCOVERED],
                UNCOVERED,
                NOW + timedelta(seconds=i),
            )
        limited = (await call(app, "GET", "/api/v1/watcher/runs?limit=1", keys.limited)).json()
        everyone = (await call(app, "GET", "/api/v1/watcher/runs?limit=1", keys.open)).json()
        other = (await call(app, "GET", f"/api/v1/watcher/runs?stack={UNCOVERED}", keys.limited)).json()
        nobody = (await call(app, "GET", "/api/v1/watcher/runs?stack=nobody/nobody", keys.limited)).json()
    assert [r["repo"] for r in everyone] == [UNCOVERED], "the newer ticks fill the page"
    assert [(r["repo"], r["job_id"]) for r in limited] == [(COVERED, JOB[COVERED])], (
        f"the limited key's page holds its own stack's tick: {limited}"
    )
    assert other == nobody == [], f"another repository's stack has the ticks of a stack with none: {other}"


#: A repository registered under the covered repository's owner that the limited key does not cover, its job, and the
#: covered owner's map part that was sent to it.
SIBLING, SIBLING_JOB, SIBLING_PART = f"{OWNER[COVERED]}/api", "job-sibling-api", "n2"


async def _sibling(db: DatabasePool) -> None:
    await db.execute("INSERT INTO repos (repo, active) VALUES ($1, true)", SIBLING)
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, created_at, pr_url) "
        "VALUES ($1, $2, 'Remove a part', 'running', $3, $4)",
        SIBLING_JOB,
        SIBLING,
        NOW,
        f"https://github.com/{SIBLING}/pull/3",
    )
    await db.execute(
        "UPDATE knowledge_maps SET graph = $2 WHERE slug = $1",
        ids(COVERED)["slug"],
        '{"nodes": [{"id": "n1", "kind": "system", "label": "n1"}, '
        f'{{"id": "{SIBLING_PART}", "kind": "system", "label": "{SIBLING_PART}", "repo": "{SIBLING}"}}], "edges": []}}',
    )
    await db.execute(
        "INSERT INTO knowledge_map_sends (map_slug, map_owner, node_id, kind, repo, job_id, sent_by) "
        "VALUES ($1, $2, $3, 'fix', $4, $5, 'ada')",
        ids(COVERED)["slug"],
        OWNER[COVERED],
        SIBLING_PART,
        SIBLING,
        SIBLING_JOB,
    )


async def test_a_map_s_work_shows_only_the_sends_to_the_key_s_repositories(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key reads its owner's map, whose parts span a repository it covers and one it does not."""
    db = db_with_migrations
    slug = ids(COVERED)["slug"]
    async with job_reads_app(db, monkeypatch) as (app, keys, _values):
        await _sibling(db)
        limited = (await call(app, "GET", f"/api/v1/maps/{slug}/work", keys.limited)).json()
        everyone = (await call(app, "GET", f"/api/v1/maps/{slug}/work", keys.open)).json()
        part = (await call(app, "GET", f"/api/v1/maps/{slug}/parts/{SIBLING_PART}", keys.limited)).json()
        part_to_everyone = (await call(app, "GET", f"/api/v1/maps/{slug}/parts/{SIBLING_PART}", keys.open)).json()
    assert sorted(everyone["work"]) == ["n1", SIBLING_PART], f"the map has work on both parts: {everyone}"
    assert sorted(limited["work"]) == ["n1"] and SIBLING_JOB not in str(limited), (
        f"the limited key reads the work sent to its own repository and none other: {limited}"
    )
    assert SIBLING_JOB in str(part_to_everyone) and SIBLING_JOB not in str(part), (
        f"the part sent to {SIBLING} shows the limited key no work: {part}"
    )


#: A repository and an owner nobody registered, each as long as the uncovered repository's and its owner's.
NOWHERE, NOWHERE_OWNER = "zyxwvu/gizmos", "zyxwvu"


def _nowhere(path: str) -> str:
    """``path`` naming :data:`NOWHERE` and :data:`NOWHERE_OWNER` instead of the uncovered repository and its owner."""
    return path.replace(UNCOVERED, NOWHERE).replace(f"/{OWNER[UNCOVERED]}", f"/{NOWHERE_OWNER}")


def _as_nowhere(a: Any, b: Any) -> Any:
    """None when the answer about the uncovered repository ``a`` is the one about :data:`NOWHERE` ``b``."""
    names = {UNCOVERED: "<repo>", OWNER[UNCOVERED]: "<owner>"}
    nowhere = {NOWHERE: "<repo>", NOWHERE_OWNER: "<owner>"}
    return same_repository_answer(a, names, b, nowhere)


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v1/repos/{UNCOVERED}/-/setup/proposal",
        f"/api/v1/repos/{UNCOVERED}",
        f"/api/v1/standards/{OWNER[UNCOVERED]}",
        f"/api/v1/standards/{OWNER[UNCOVERED]}/design/repos/{UNCOVERED}",
        f"/api/v1/repos/{UNCOVERED}/-/readiness",
        f"/api/v1/orgs/{OWNER[UNCOVERED]}/context-audit",
    ],
)
async def test_a_view_names_no_job_of_a_repository_the_key_does_not_cover(
    path: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A view of a repository the key does not cover, or of an owner it does not cover as a whole, names none of its
    jobs: the setup job, a pull request a job opened, a standard's last audit, the owner's context audits. Since the
    repository reads, each answers as the view of a repository, or an owner, nobody registered
    (``test_repository_reads.py``); a key without a list reads the job there, and the key its own."""
    async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        await db_with_migrations.execute(
            "UPDATE organizations SET settings = $2 WHERE owner = $1", OWNER[UNCOVERED], {"context_audit_days": 7}
        )
        limited = await call(app, "GET", path, keys.limited)
        nowhere = await call(app, "GET", _nowhere(path), keys.limited)
        everyone = await call(app, "GET", path, keys.open)
        own = await call(
            app, "GET", path.replace(UNCOVERED, COVERED).replace(OWNER[UNCOVERED], OWNER[COVERED]), keys.limited
        )
    named = (JOB[UNCOVERED], audit_job(UNCOVERED), f"github.com/{UNCOVERED}/pull")
    assert any(n in everyone.text for n in named), (
        f"{path} names the job to a key without a list: {everyone.text[:300]}"
    )
    assert not any(n in limited.text for n in named), f"{path} names no job to the limited key: {limited.text[:300]}"
    differ = _as_nowhere(limited, nowhere)
    assert differ is None, f"{path} reads to the limited key as for a repository nobody registered: {differ}"
    assert any(n in own.text for n in (JOB[COVERED], audit_job(COVERED), f"github.com/{COVERED}/pull")), (
        f"{path} names the limited key's own job: {own.text[:300]}"
    )
