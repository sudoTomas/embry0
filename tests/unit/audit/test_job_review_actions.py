"""Job review is recorded as ``job.review.*``; the rows written as the world's ``quest.*`` still read and filter.

Reviewing a job moved out of the game layer, and its audit actions with it.
An audit row's action never changes, so the old rows keep their names: the
action list still names them, with the action that replaced each, and a
filter by the new name finds them too.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.storage.repositories.audit import _where
from tests.unit.audit.test_action_registry import _scan

#: The world's names for the review actions, each with the job review action that replaced it.
RENAMED = {
    "quest.evaluated": "job.review.evaluated",
    "quest.sent_back": "job.review.sent_back",
    "quest.rejected": "job.review.rejected",
    "quest.restarted": "job.review.restarted",
    "quest.dismissed": "job.review.dismissed",
}


def test_reviews_are_written_only_under_their_job_review_names() -> None:
    written, problems = _scan()
    assert problems == [], problems
    names = {action for _where, action in written}
    still = sorted({f"{where} {action}" for where, action in written if action in RENAMED})
    assert still == [], f"the world's old review actions are still written: {still}"
    missing = sorted(set(RENAMED.values()) - names)
    assert missing == [], f"no writer records {missing}"


@pytest.mark.asyncio
async def test_the_action_list_still_names_the_old_review_actions_with_what_replaced_them() -> None:
    from embry0.api.app import create_app
    from embry0.config import Embry0Config

    @asynccontextmanager
    async def _noop(_app):
        yield

    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config, lifespan_override=_noop)
    app.state.config = config
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/audit/actions")
    assert resp.status_code == 200, resp.text
    listed = {entry["action"]: entry for entry in resp.json()["actions"]}
    for old, new in RENAMED.items():
        assert old in listed, f"{old} is no longer listed, so its rows read as nothing"
        assert listed[old].get("replaced_by") == new, f"{old} does not name {new}: {listed[old]}"
        assert listed[old]["label"] == listed[new]["label"], f"{old} reads otherwise than {new}: {listed[old]}"
        assert listed[new].get("replaced_by") is None, listed[new]


def test_a_filter_by_a_job_review_action_also_finds_its_rows_from_before_the_rename() -> None:
    where, args = _where(None, "job.review.sent_back", None, None)
    assert (where, args) == (" WHERE action = ANY($1)", [["job.review.sent_back", "quest.sent_back"]]), (
        f"the filter by the new name misses the old rows: {where!r} {args!r}"
    )
    assert _where(None, "quest.sent_back", None, None) == (" WHERE action = $1", ["quest.sent_back"]), (
        "the old name still filters exactly"
    )
    assert _where(None, "user.login", None, None) == (" WHERE action = $1", ["user.login"])
