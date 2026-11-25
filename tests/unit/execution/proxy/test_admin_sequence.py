"""A proxy takes an admin request for a sandbox or a credential only when its number is newer than the last it took
for the same one: a late or replayed older request can never undo a newer one (embry0.execution.proxy.enrollment).

Each is refused with 409 (``stale_request``) and counted; a request without a number, from an orchestrator that
predates them, is taken as before.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp.test_utils import TestClient

from embry0.execution.proxy.enrollment import ENROLLMENT
from embry0.execution.proxy.git_proxy import create_git_proxy_app
from embry0.execution.proxy.metering import METER
from embry0.execution.proxy.xai_proxy import TOKEN_HOLDER, TOKENS, create_xai_proxy_app

ADMIN = "test-admin-secret-not-real"
TOKEN = "sandbox-bearer-token-at-least-32-chars-long"
H = {"X-Admin-Token": ADMIN}


async def _xai(aiohttp_client: Any) -> TestClient:
    return await aiohttp_client(create_xai_proxy_app(ADMIN))


async def _enroll(client: TestClient, seq: int | None, token: str = TOKEN, sandbox: str = "sbx-1") -> Any:
    body: dict[str, Any] = {"sandbox_id": sandbox, "sandbox_token": token}
    if seq is not None:
        body["seq"] = seq
    return await client.post("/admin/enroll", json=body, headers=H)


async def test_an_enrollment_not_newer_than_the_last_taken_is_refused_and_counted(aiohttp_client: Any) -> None:
    client = await _xai(aiohttp_client)
    first = await _enroll(client, 5)
    assert (first.status, (await first.json())["seq"]) == (200, 5), "a taken request is echoed with its number"
    for older in (5, 4):
        resp = await _enroll(client, older, token="other-bearer-token-at-least-32-chars-long")
        assert resp.status == 409 and (await resp.json())["error"] == "stale_request", f"seq {older} was taken"
    meter = await (await client.get("/admin/meter", headers=H)).json()
    assert meter.get("stale_refused") == 2, "the refusals were not counted"
    newer = await _enroll(client, 6, token="newest-bearer-token-at-least-32-chars-long")
    assert newer.status == 200
    registry = client.app[ENROLLMENT]
    assert registry.lookup("newest-bearer-token-at-least-32-chars-long") == "sbx-1"
    assert registry.lookup("other-bearer-token-at-least-32-chars-long") is None, "a refused enrollment was applied"


async def test_a_request_without_a_number_is_taken_and_a_bad_one_refused(aiohttp_client: Any) -> None:
    client = await _xai(aiohttp_client)
    assert (await _enroll(client, None)).status == 200, "an orchestrator that predates numbers was refused"
    for bad in ("7", True, -1, 1.5):
        resp = await client.post(
            "/admin/enroll", json={"sandbox_id": "sbx-1", "sandbox_token": TOKEN, "seq": bad}, headers=H
        )
        assert resp.status == 400, f"seq {bad!r} was taken"


async def test_the_order_outlives_the_enrollment(aiohttp_client: Any) -> None:
    """A late enrollment of a sandbox ended since is refused: the end was newer."""
    client = await _xai(aiohttp_client)
    await _enroll(client, 10)
    stale_end = await client.delete("/admin/enroll/sbx-1", json={"seq": 9}, headers=H)
    assert stale_end.status == 409, "an older end undid a newer enrollment"
    assert (await client.delete("/admin/enroll/sbx-1", json={"seq": 12}, headers=H)).status == 200
    assert (await _enroll(client, 11)).status == 409, "a late enrollment brought an ended sandbox back"
    assert (await client.delete("/admin/enroll/sbx-1", headers=H)).status == 200, "an end without a number was refused"


async def test_a_run_s_declaration_and_end_are_ordered_with_its_sandbox_s_enrollment(aiohttp_client: Any) -> None:
    client = await _xai(aiohttp_client)
    await _enroll(client, 1)
    declared = await client.post("/admin/runs", json={"sandbox_id": "sbx-1", "run_id": "run-A", "seq": 3}, headers=H)
    assert (await declared.json())["seq"] == 3, "the declaration did not echo its number"
    await client.post("/admin/runs", json={"sandbox_id": "sbx-1", "run_id": "run-B", "seq": 5}, headers=H)
    late = await client.post("/admin/runs", json={"sandbox_id": "sbx-1", "run_id": "run-A", "seq": 3}, headers=H)
    assert late.status == 409 and client.app[METER]._current["sbx-1"] == "run-B", "a late declaration took the sandbox"
    assert (await _enroll(client, 2)).status == 409, "a late enrollment ended the sandbox's newer run"
    assert (await client.delete("/admin/runs/run-B", json={"seq": 4}, headers=H)).status == 409
    ended = await client.delete("/admin/runs/run-B", json={"seq": 7}, headers=H)
    assert ended.status == 200 and (await ended.json())["tally"]["run_id"] == "run-B"
    late_c = await client.post("/admin/runs", json={"sandbox_id": "sbx-1", "run_id": "run-C", "seq": 6}, headers=H)
    assert late_c.status == 409, "a declaration decided before the last run's end was taken after it"
    meter = await (await client.get("/admin/meter", headers=H)).json()
    assert meter["stale_refused"] == 4


async def test_a_credential_s_token_pushes_are_ordered_apart_from_others(aiohttp_client: Any) -> None:
    client = await _xai(aiohttp_client)
    push = {"credential_id": "cred-1", "access_token": "at-new", "seq": 5}
    assert (await client.post("/admin/token", json=push, headers=H)).status == 200
    late = {"credential_id": "cred-1", "access_token": "at-old", "seq": 4}
    assert (await client.post("/admin/token", json=late, headers=H)).status == 409
    assert client.app[TOKENS]["cred-1"]["access_token"] == "at-new", "an older token replaced a newer one"
    other = {"credential_id": "cred-2", "access_token": "at-2", "seq": 1}
    assert (await client.post("/admin/token", json=other, headers=H)).status == 200, "another credential's order"
    unnamed = {"access_token": "at-global", "seq": 2}
    assert (await client.post("/admin/token", json=unnamed, headers=H)).status == 200
    assert client.app[TOKEN_HOLDER]["access_token"] == "at-global"


async def test_a_change_of_push_branches_is_ordered_with_the_enrollment(aiohttp_client: Any) -> None:
    client = await aiohttp_client(create_git_proxy_app(github_token="", admin_token=ADMIN))
    enroll = {
        "sandbox_id": "sbx-1",
        "sandbox_token": TOKEN,
        "github_token": "forge-token-not-real",
        "git_host": "127.0.0.1",
        "allowed_repos": ["acme/widgets"],
        "git_upstream": "http://127.0.0.1:1",
        "git_push": True,
        "git_push_branches": ["embry0/job-1"],
        "job_id": "job-1a2b3c4d5e6f",
        "seq": 10,
    }
    assert (await client.post("/admin/enroll", json=enroll, headers=H)).status == 200
    stale = await client.put(
        "/admin/git-push-branches/sbx-1", json={"git_push_branches": ["embry0/other"], "seq": 9}, headers=H
    )
    assert stale.status == 409, "an older change of push branches was applied"
    newer = await client.put(
        "/admin/git-push-branches/sbx-1", json={"git_push_branches": ["embry0/next"], "seq": 11}, headers=H
    )
    assert newer.status == 200 and (await newer.json())["git_push_branches"] == ["embry0/next"]


def test_the_order_is_bounded() -> None:
    from embry0.execution.proxy.enrollment import AdminSequence

    sequence = AdminSequence(max_keys=2)
    for n, key in enumerate(("sandbox:a", "sandbox:b", "sandbox:c")):
        sequence.take(key, n + 10)
    assert sequence.newer("sandbox:a", 0), "the oldest key outlived the bound"
    assert not sequence.newer("sandbox:c", 12)


@pytest.mark.parametrize("seq", [5, 6])
def test_a_number_is_taken_only_once_it_is_applied(seq: int) -> None:
    from embry0.execution.proxy.enrollment import AdminSequence

    sequence = AdminSequence()
    assert sequence.newer("sandbox:a", seq)
    assert sequence.newer("sandbox:a", seq), "checking a number recorded it"
    sequence.take("sandbox:a", seq)
    assert not sequence.newer("sandbox:a", seq)


def test_the_last_number_taken_never_goes_back() -> None:
    from embry0.execution.proxy.enrollment import AdminSequence

    sequence = AdminSequence()
    sequence.take("sandbox:a", 5)
    sequence.take("sandbox:a", 3)
    assert not sequence.newer("sandbox:a", 4), "taking an older number made an older request newer"


def test_the_sandbox_evicted_is_the_one_taken_least_recently() -> None:
    from embry0.execution.proxy.enrollment import AdminSequence

    sequence = AdminSequence(max_keys=2)
    sequence.take("sandbox:a", 1)
    sequence.take("sandbox:b", 2)
    sequence.take("sandbox:a", 3)  # a is the most recent now
    sequence.take("sandbox:c", 4)
    assert not sequence.newer("sandbox:a", 3), "the sandbox taken most recently was evicted"
    assert sequence.newer("sandbox:b", 0), "the sandbox taken least recently outlived the bound"
