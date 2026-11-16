"""``DELETE /issues/{id}`` acts on the whole subtree under the issue (#563), over a real database.

A delete cancelled the issue and its direct children only: a grandchild
stayed open under a cancelled child, and its active jobs kept running. Every
issue under the issue now goes through what a child did: the reach check, the
status check (one closed or cancelled keeps its status, one no transition
cancels refuses the delete), all before anything is written, in one write. The
walk is bounded: past ``MAX_DELETE_DEPTH`` levels or ``MAX_DELETE_DESCENDANTS``
issues the delete changes nothing and answers 409.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

import embry0.api.v1.issues as issues_api

pytestmark = pytest.mark.requires_postgres


async def _issue(api_client: Any) -> str:
    created = await api_client.post(
        "/api/v1/issues", json={"repo": "owner/repo", "title": "T", "body": "", "auto_triage": False}
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _under(api_client: Any, tree: dict[str, tuple[str, str]]) -> None:
    """Issues ``id -> (parent, status)``, written as a decomposition or a tracker leaves them, parents first."""
    db = api_client.app.state.db
    for issue_id, (parent, status) in tree.items():
        await db.execute(
            "INSERT INTO issues (id, title, body, labels, repo, status, parent_issue_id) "
            "VALUES ($1, $1, '', '[]'::jsonb, 'owner/repo', $2, $3)",
            issue_id,
            status,
            parent,
        )


async def _statuses(api_client: Any, ids: list[str]) -> dict[str, str]:
    rows = await api_client.app.state.db.fetch("SELECT id, status FROM issues WHERE id = ANY($1::text[])", ids)
    return {str(r["id"]): str(r["status"]) for r in rows}


async def _delete(api_client: Any, issue_id: str) -> Any:
    try:
        return await api_client.delete(f"/api/v1/issues/{issue_id}")
    except ValueError as exc:  # the test client raises what the app did not answer: a 500 in production
        pytest.fail(f"the delete raised instead of answering: {exc}")


async def test_every_issue_under_the_issue_is_cancelled_or_keeps_its_end(api_client: Any) -> None:
    root = await _issue(api_client)
    tree = {
        "kid": (root, "open"),
        "grandkid": ("kid", "open"),
        "grandkid-closed": ("kid", "closed"),
        "great-grandkid": ("grandkid-closed", "in_progress"),
        "great-grandkid-cancelled": ("grandkid", "cancelled"),
    }
    await _under(api_client, tree)

    deleted = await _delete(api_client, root)

    assert deleted.status_code == 204, f"the delete succeeds: {deleted.status_code} {deleted.text}"
    after = await _statuses(api_client, [root, *tree])
    assert after == {
        root: "cancelled",
        "kid": "cancelled",
        "grandkid": "cancelled",
        "grandkid-closed": "closed",
        "great-grandkid": "cancelled",
        "great-grandkid-cancelled": "cancelled",
    }, f"every issue under the issue not closed or cancelled is cancelled, at any depth: {after}"


async def test_a_grandchild_s_active_job_is_cancelled(api_client: Any) -> None:
    root = await _issue(api_client)
    await _under(api_client, {"kid": (root, "open"), "grandkid": ("kid", "in_progress")})
    db = api_client.app.state.db
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, issue_id) "
        "VALUES ('job-of-the-grandkid', 'owner/repo', 'work', 'running', 'full-magnum-opus', 'grandkid')"
    )
    api_client.app.state.issue_executor = MagicMock(cancel_job=AsyncMock())

    deleted = await _delete(api_client, root)

    assert deleted.status_code == 204, deleted.text
    cancelled = [c.args[0] for c in api_client.app.state.issue_executor.cancel_job.await_args_list]
    assert cancelled == ["job-of-the-grandkid"], f"the grandchild's running job is cancelled too: {cancelled}"


async def test_a_grandchild_no_transition_cancels_refuses_the_delete_and_changes_nothing(api_client: Any) -> None:
    root = await _issue(api_client)
    tree = {"kid": (root, "open"), "grandkid": ("kid", "archived")}
    await _under(api_client, tree)
    before = await _statuses(api_client, [root, *tree])

    refused = await _delete(api_client, root)

    assert refused.status_code == 409, f"a grandchild no transition cancels is refused: {refused.status_code}"
    assert "grandkid" in refused.json()["detail"] and "'archived'" in refused.json()["detail"], refused.text
    assert await _statuses(api_client, [root, *tree]) == before, "a refused delete changes no issue"


@pytest.mark.parametrize(
    ("bound", "limit", "tree", "refused"),
    [
        ("MAX_DELETE_DEPTH", 2, {"a": ("ROOT", "open"), "b": ("a", "open")}, False),
        ("MAX_DELETE_DEPTH", 2, {"a": ("ROOT", "open"), "b": ("a", "open"), "c": ("b", "open")}, True),
        ("MAX_DELETE_DESCENDANTS", 3, {"a": ("ROOT", "open"), "b": ("ROOT", "open"), "c": ("a", "open")}, False),
        (
            "MAX_DELETE_DESCENDANTS",
            3,
            {"a": ("ROOT", "open"), "b": ("ROOT", "open"), "c": ("a", "open"), "d": ("c", "open")},
            True,
        ),
    ],
    ids=["depth-at-the-bound", "depth-past-it", "count-at-the-bound", "count-past-it"],
)
async def test_a_subtree_past_the_bounds_is_refused_and_nothing_changes(
    api_client: Any, monkeypatch: pytest.MonkeyPatch, bound: str, limit: int, tree: dict[str, Any], refused: bool
) -> None:
    """Up to its bounds a delete acts on the whole subtree; past either it acts on none of it, rather than on the part
    it read as if that were all."""
    monkeypatch.setattr(issues_api, bound, limit, raising=False)
    root = await _issue(api_client)
    await _under(api_client, {i: (root if p == "ROOT" else p, s) for i, (p, s) in tree.items()})

    answer = await _delete(api_client, root)
    after = await _statuses(api_client, [root, *tree])

    if refused:
        assert answer.status_code == 409, f"{bound} {limit}, a subtree past it: {answer.status_code} {after}"
        assert "Nothing was changed" in answer.json()["detail"], answer.text
        assert set(after.values()) == {"open"}, f"a refused delete changes no issue: {after}"
    else:
        assert answer.status_code == 204, f"{bound} {limit}, a subtree at it: {answer.status_code} {answer.text}"
        assert set(after.values()) == {"cancelled"}, f"every issue of a subtree at the bound is cancelled: {after}"


async def test_a_parent_cycle_written_by_hand_ends_the_walk(api_client: Any) -> None:
    """``parent_issue_id`` can only loop when written outside embry0; the walk does not follow it back round."""
    root = await _issue(api_client)
    await _under(api_client, {"a": (root, "open"), "b": ("a", "open")})
    await api_client.app.state.db.execute("UPDATE issues SET parent_issue_id = 'b' WHERE id = $1", root)

    deleted = await _delete(api_client, root)

    assert deleted.status_code == 204, deleted.text
    after = await _statuses(api_client, [root, "a", "b"])
    assert after == {root: "cancelled", "a": "cancelled", "b": "cancelled"}, (
        f"each issue of the loop is cancelled once, and the walk ends: {after}"
    )


# ---- what changes while the delete cancels the jobs (#578 review) ----------------------------------------


async def _job(api_client: Any, job_id: str, issue_id: str, status: str, *, minutes_ago: int = 0) -> None:
    await api_client.app.state.db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, issue_id, created_at) "
        "VALUES ($1, 'owner/repo', 'work', $2, 'full-magnum-opus', $3, NOW() - make_interval(mins => $4))",
        job_id,
        status,
        issue_id,
        minutes_ago,
    )


async def test_an_issue_moved_out_from_under_the_issue_meanwhile_is_not_cancelled(api_client: Any) -> None:
    """The jobs are cancelled before the write, through the executor, which takes a while. An issue re-parented under
    another issue meanwhile is no longer this delete's: the write walks the subtree again and leaves it."""
    root = await _issue(api_client)
    elsewhere = await _issue(api_client)
    await _under(api_client, {"kid": (root, "open"), "grandkid": ("kid", "open"), "done": (root, "closed")})
    await _job(api_client, "job-of-the-root", root, "running")
    db = api_client.app.state.db

    async def cancel_and_move(job_id: str, *, actor: str) -> None:
        await db.execute("UPDATE issues SET parent_issue_id = $1 WHERE id = 'kid'", elsewhere)

    api_client.app.state.issue_executor = MagicMock(cancel_job=AsyncMock(side_effect=cancel_and_move))

    with capture_logs() as logs:
        deleted = await _delete(api_client, root)

    assert deleted.status_code == 204, deleted.text
    after = await _statuses(api_client, [root, "kid", "grandkid", "done", elsewhere])
    assert after == {root: "cancelled", "kid": "open", "grandkid": "open", "done": "closed", elsewhere: "open"}, (
        f"an issue moved under another issue while the jobs were cancelled is left as it is, its own under it: {after}"
    )
    left = [(e["issue_id"], e["left"]) for e in logs if e["event"] == "issue_delete_left_issues"]
    assert left == [(root, ["grandkid", "kid"])], (
        f"the issues the delete checked and left are logged, not the one it left closed on purpose: {left}"
    )


async def test_an_issue_past_the_first_page_of_jobs_has_its_active_job_cancelled(api_client: Any) -> None:
    """Jobs were read a page of 100 at a time, newest first, so an active job older than an issue's last 100 was
    neither reach-checked nor cancelled."""
    root = await _issue(api_client)
    await _job(api_client, "job-still-running", root, "running", minutes_ago=500)
    for n in range(100):
        await _job(api_client, f"job-done-{n:03d}", root, "completed", minutes_ago=n)
    api_client.app.state.issue_executor = MagicMock(cancel_job=AsyncMock())

    deleted = await _delete(api_client, root)

    assert deleted.status_code == 204, deleted.text
    cancelled = [c.args[0] for c in api_client.app.state.issue_executor.cancel_job.await_args_list]
    assert cancelled == ["job-still-running"], f"the active job behind 100 newer ones is cancelled: {cancelled}"


async def test_an_issue_moved_to_a_repository_the_key_does_not_cover_meanwhile_is_not_cancelled(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key with a repository allowlist passed the reach check; an issue under the one it deletes then moved to a
    repository the key does not cover while the jobs were cancelled. The write is the key's too: it leaves that
    issue alone."""
    from tests.fixtures.issue_reads import issue_reads_app
    from tests.fixtures.repository_writes import COVERED, UNCOVERED, call

    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        for issue_id, parent in (("del-root", None), ("del-kid", "del-root")):
            await db.execute(
                "INSERT INTO issues (id, title, body, labels, repo, status, parent_issue_id) "
                "VALUES ($1, $1, '', '[]'::jsonb, $2, 'open', $3)",
                issue_id,
                COVERED,
                parent,
            )
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, issue_id) "
            "VALUES ('job-del-root', $1, 'work', 'running', 'full-magnum-opus', 'del-root')",
            COVERED,
        )

        async def cancel_and_move(job_id: str, *, actor: str) -> None:
            await db.execute("UPDATE issues SET repo = $1 WHERE id = 'del-kid'", UNCOVERED)

        app.state.issue_executor = MagicMock(cancel_job=AsyncMock(side_effect=cancel_and_move))
        deleted = await call(app, "DELETE", "/api/v1/issues/del-root", keys.limited)
        rows = await db.fetch("SELECT id, repo, status FROM issues WHERE id = ANY($1::text[])", ["del-root", "del-kid"])

    assert deleted.status_code == 204, deleted.text
    after = {r["id"]: (r["repo"], r["status"]) for r in rows}
    assert after == {"del-root": (COVERED, "cancelled"), "del-kid": (UNCOVERED, "open")}, (
        f"the key's delete wrote nothing in a repository it does not cover: {after}"
    )


async def test_an_issue_put_under_the_issue_meanwhile_is_not_cancelled(api_client: Any) -> None:
    """The write takes only issues the delete checked: one put under the issue while the jobs were cancelled went
    through no reach or status check, and is left as it is."""
    root = await _issue(api_client)
    await _under(api_client, {"kid": (root, "open")})
    await _job(api_client, "job-of-the-root", root, "running")

    async def cancel_and_add(job_id: str, *, actor: str) -> None:
        await _under(api_client, {"newcomer": ("kid", "open")})

    api_client.app.state.issue_executor = MagicMock(cancel_job=AsyncMock(side_effect=cancel_and_add))

    deleted = await _delete(api_client, root)

    assert deleted.status_code == 204, deleted.text
    after = await _statuses(api_client, [root, "kid", "newcomer"])
    assert after == {root: "cancelled", "kid": "cancelled", "newcomer": "open"}, (
        f"an issue put under the issue after the checks is not written: {after}"
    )


async def test_the_write_walks_no_more_issues_than_the_check_did(api_client: Any) -> None:
    """The cancel statement walks the subtree again with the check's count bound too: a subtree that grew during the
    jobs' cancels is walked no further than the issue and ``max_count`` issues under it."""
    from embry0.storage.repositories.issues import IssuesRepository

    root = await _issue(api_client)
    kids = {f"kid-{n}": (root, "open") for n in range(4)}
    await _under(api_client, kids)
    issues = IssuesRepository(api_client.app.state.db)

    cancelled = await issues.cancel_subtree(root, [root, *kids], max_depth=10, max_count=1)

    assert len(cancelled) <= 2, f"the issue and at most one issue under it are walked: {cancelled}"
