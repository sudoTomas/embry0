"""``DELETE /issues/{id}`` and issues that are already closed or cancelled (#550), over a real database.

A delete succeeds: the issue and its direct children are cancelled, except
any already closed or cancelled, which keep their status. Every status they
have is checked before anything is cancelled or written; only one no
transition can cancel (a row written outside embry0) is refused, with
nothing changed. On main a closed issue answered 500, and a closed child
failed the delete partway: the children before it were cancelled, the issue
was not.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.requires_postgres

#: Every status an issue can have, a child in each.
STATUSES = ["open", "triaging", "in_progress", "awaiting_input", "paused", "closed", "cancelled"]


async def _issue(api_client: Any) -> str:
    created = await api_client.post(
        "/api/v1/issues", json={"repo": "owner/repo", "title": "T", "body": "", "auto_triage": False}
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _children(api_client: Any, parent: str, statuses: list[str]) -> dict[str, str]:
    """A child of ``parent`` in each of ``statuses``, written as a tracker or a job would leave it: its id to its
    status."""
    db = api_client.app.state.db
    children = {}
    for n, status in enumerate(statuses):
        child = f"{parent}-kid-{n}"
        await db.execute(
            "INSERT INTO issues (id, title, body, labels, repo, status, parent_issue_id) "
            "VALUES ($1, $1, '', '[]'::jsonb, 'owner/repo', $2, $3)",
            child,
            status,
            parent,
        )
        children[child] = status
    return children


async def _statuses(api_client: Any, ids: list[str]) -> dict[str, str]:
    rows = await api_client.app.state.db.fetch("SELECT id, status FROM issues WHERE id = ANY($1::text[])", ids)
    return {str(r["id"]): str(r["status"]) for r in rows}


async def _delete(api_client: Any, issue_id: str) -> Any:
    try:
        return await api_client.delete(f"/api/v1/issues/{issue_id}")
    except ValueError as exc:  # the test client raises what the app did not answer: a 500 in production
        pytest.fail(f"the delete raised instead of answering: {exc}")


async def test_deleting_a_closed_issue_keeps_it_closed(api_client: Any) -> None:
    issue = await _issue(api_client)
    closed = await api_client.put(f"/api/v1/issues/{issue}", json={"status": "closed"})
    assert closed.status_code == 200, closed.text

    deleted = await _delete(api_client, issue)

    assert deleted.status_code == 204, f"deleting a closed issue succeeds: {deleted.status_code} {deleted.text}"
    assert await _statuses(api_client, [issue]) == {issue: "closed"}, "a closed issue keeps its status"


@pytest.mark.parametrize("parent_status", ["open", "closed"])
async def test_a_delete_cancels_the_issues_not_ended_and_keeps_the_ended_ones(
    api_client: Any, parent_status: str
) -> None:
    issue = await _issue(api_client)
    if parent_status != "open":
        await api_client.put(f"/api/v1/issues/{issue}", json={"status": parent_status})
    children = await _children(api_client, issue, STATUSES)

    deleted = await _delete(api_client, issue)

    assert deleted.status_code == 204, f"the delete succeeds: {deleted.status_code} {deleted.text}"
    expected = {
        child: status if status in ("closed", "cancelled") else "cancelled" for child, status in children.items()
    }
    expected[issue] = "closed" if parent_status == "closed" else "cancelled"
    after = await _statuses(api_client, [issue, *children])
    assert after == expected, (
        f"every issue not closed or cancelled is cancelled, and the others keep their status: {after}"
    )


async def test_a_status_no_transition_cancels_refuses_the_delete_and_changes_nothing(api_client: Any) -> None:
    issue = await _issue(api_client)
    children = await _children(api_client, issue, ["open", "archived", "in_progress"])
    before = await _statuses(api_client, [issue, *children])

    refused = await _delete(api_client, issue)

    assert refused.status_code == 409, f"a status no transition cancels is refused: {refused.status_code}"
    detail = refused.json()["detail"]
    assert f"{issue}-kid-1" in detail and "'archived'" in detail and "Nothing was changed" in detail, detail
    assert await _statuses(api_client, [issue, *children]) == before, "a refused delete changes no issue"


async def test_an_issue_closed_while_the_delete_cancels_its_jobs_stays_closed(api_client: Any) -> None:
    """The status each issue has when the write runs decides: here a child closes while its job is being cancelled
    (a forge's close arriving then), and the delete leaves it closed rather than failing on it."""
    issue = await _issue(api_client)
    (child,) = await _children(api_client, issue, ["in_progress"])
    db = api_client.app.state.db
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, issue_id) "
        "VALUES ('job-of-the-child', 'owner/repo', 'work', 'running', 'full-magnum-opus', $1)",
        child,
    )

    async def _cancel(job_id: str, *, actor: str) -> None:
        await db.execute("UPDATE jobs SET status = 'cancelled' WHERE job_id = $1", job_id)
        await db.execute("UPDATE issues SET status = 'closed' WHERE id = $1", child)

    api_client.app.state.issue_executor = MagicMock(cancel_job=AsyncMock(side_effect=_cancel))

    deleted = await _delete(api_client, issue)

    assert deleted.status_code == 204, f"the delete succeeds: {deleted.status_code} {deleted.text}"
    api_client.app.state.issue_executor.cancel_job.assert_awaited_once()
    assert await _statuses(api_client, [issue, child]) == {issue: "cancelled", child: "closed"}, (
        "an issue closed once the delete had read it keeps its status"
    )


async def _cancelled_rows(api_client: Any) -> list[str]:
    rows = await api_client.app.state.db.fetch(
        "SELECT issue_id FROM audit_log WHERE action = 'issue.cancelled' ORDER BY issue_id"
    )
    return [str(r["issue_id"]) for r in rows]


async def test_the_audit_names_each_issue_the_delete_cancelled_and_no_other(api_client: Any) -> None:
    """One `issue.cancelled` row for each issue the delete cancelled: none for a child it left closed or cancelled,
    none for a closed issue, and none for a delete repeated on an issue already cancelled."""
    issue = await _issue(api_client)
    children = await _children(api_client, issue, ["open", "closed", "cancelled", "in_progress"])
    closed = await _issue(api_client)
    await api_client.put(f"/api/v1/issues/{closed}", json={"status": "closed"})

    for target in (issue, closed, issue):
        deleted = await _delete(api_client, target)
        assert deleted.status_code == 204, deleted.text

    cancelled = sorted([issue, *(c for c, status in children.items() if status in ("open", "in_progress"))])
    rows = await _cancelled_rows(api_client)
    assert rows == cancelled, f"an audit row for each issue cancelled, once, and for no other: {rows}"
