"""POST /merge-approvals/{id}/decide and the reads, on the real app + database."""

from __future__ import annotations

import json

import pytest

from embry0.services.merge_approvals import MergeApprovalService
from embry0.storage.repositories.api_keys import ApiKeysRepository
from embry0.storage.repositories.merge_approvals import MergeApprovalsRepository
from tests.unit.services.test_merge_approvals import CHAT, PR_URL, FakeGitHub, FakeNotifier, _config

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def _wire(api_client, **config):
    db = api_client.app.state.db
    await db.execute("DELETE FROM merge_approvals")
    github, notifier = FakeGitHub(), FakeNotifier()
    service = MergeApprovalService(
        repo=MergeApprovalsRepository(db), github=github, notifier=notifier, config=_config(**config)
    )
    api_client.app.state.merge_approvals = service
    row = await service.ask(job_id="job-api", pr_url=PR_URL)
    assert row is not None
    return service, github, row


async def test_decide_relays_an_approval(api_client) -> None:
    _, github, row = await _wire(api_client)
    resp = await api_client.post(
        f"/api/v1/merge-approvals/{row['id']}/decide",
        json={"decision": "approve", "decided_by": "Alex", "chat_id": CHAT},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "would_merge"
    assert body["decided_by"] == "Alex"
    assert github.merge_calls == []

    audit = await api_client.app.state.db.fetchrow(
        "SELECT details FROM audit_log WHERE action = 'merge.decided' ORDER BY id DESC LIMIT 1"
    )
    assert audit is not None, "the decision left no audit row"
    details = audit["details"] if isinstance(audit["details"], dict) else json.loads(audit["details"])
    assert details["via"] == "chat", "the relay's form is the chat channel"


async def test_decide_from_the_wrong_chat_is_403(api_client) -> None:
    _, _, row = await _wire(api_client)
    resp = await api_client.post(
        f"/api/v1/merge-approvals/{row['id']}/decide",
        json={"decision": "approve", "decided_by": "x", "chat_id": "19:elsewhere@thread.v2"},
    )
    assert resp.status_code == 403


async def test_decide_on_an_unknown_ask_is_404(api_client) -> None:
    await _wire(api_client)
    resp = await api_client.post(
        "/api/v1/merge-approvals/mrg-nope/decide",
        json={"decision": "approve", "decided_by": "x", "chat_id": CHAT},
    )
    assert resp.status_code == 404


async def test_reads(api_client) -> None:
    _, _, row = await _wire(api_client)
    listed = await api_client.get("/api/v1/merge-approvals", params={"status": "pending"})
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()["items"]] == [row["id"]]
    one = await api_client.get(f"/api/v1/merge-approvals/{row['id']}")
    assert one.json()["head_sha"] == row["head_sha"]
    assert (await api_client.get("/api/v1/merge-approvals/mrg-nope")).status_code == 404


async def test_without_the_service_the_routes_say_so(api_client) -> None:
    api_client.app.state.merge_approvals = None
    assert (await api_client.get("/api/v1/merge-approvals")).status_code == 503


async def test_a_merge_scope_key_can_be_stored(api_client) -> None:
    """The api_keys CHECK constraint must admit the new scope (migration 78)."""
    repo = ApiKeysRepository(api_client.app.state.db)
    await api_client.app.state.db.execute("DELETE FROM api_keys WHERE name = 'hermes-merge-test'")
    row, _ = await repo.create(name="hermes-merge-test", scope="merge", user_id=None, created_by="test")
    assert row.scope == "merge"


async def test_the_dashboard_form_decides_with_no_chat_configured(api_client) -> None:
    """Spec 3.8: body {decision} alone is the dashboard's form. The route names
    the actor itself and stores the literal channel. (api_client runs in
    AUTH_DEV_MODE, where the admin gate admits and the actor is the anonymous
    one; the tier itself is pinned in test_merge_decide_dashboard_gate.py.)"""
    _, github, row = await _wire(api_client, merge_approval_chat_id="")
    resp = await api_client.post(f"/api/v1/merge-approvals/{row['id']}/decide", json={"decision": "approve"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "would_merge"
    assert github.merge_calls == []
    stored = await api_client.app.state.merge_approvals.get(row["id"])
    assert stored["chat_id"] == "dashboard"
    assert stored["decided_by"] == body["decided_by"] != ""
    audit = await api_client.app.state.db.fetchrow(
        "SELECT details FROM audit_log WHERE action = 'merge.decided' ORDER BY id DESC LIMIT 1"
    )
    details = audit["details"] if isinstance(audit["details"], dict) else json.loads(audit["details"])
    assert (details["via"], details["decided_by"]) == ("dashboard", body["decided_by"])


async def test_half_a_relay_body_is_422(api_client) -> None:
    _, _, row = await _wire(api_client)
    resp = await api_client.post(
        f"/api/v1/merge-approvals/{row['id']}/decide", json={"decision": "approve", "decided_by": "Alex"}
    )
    assert resp.status_code == 422, resp.text
    assert "together" in resp.json()["detail"]
    assert (await api_client.app.state.merge_approvals.get(row["id"]))["status"] == "pending"
