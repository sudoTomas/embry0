"""Every write route that acts on one decision, proposal, hand-off or ask applies the key's repository allowlist.

Each such route is called through a key whose allowlist covers one of two
repositories (:mod:`tests.fixtures.offered_work`): once on the other
repository's item, once on an item that does not exist, with the same body.
The two answers must be the same, the item's own id aside, so an answer never
tells whether the item exists. The refusal is logged and audited all the same.
Through a key without an allowlist the route must reach the item, so the
check measures something.

A route is found by the item its path names (:data:`ITEMS`); a new one fails
until it has a body here (:data:`BODIES`). :data:`NOT_ITEMS` lists the write
routes whose path names an item of another kind, with why.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, Request
from httpx import ASGITransport, AsyncClient

from embry0.api import repo_allowlist
from tests.fixtures.offered_work import JOBS, LIMITED, OPEN, PROPOSALS, UNCOVERED, offered_work_app
from tests.fixtures.routes import app_routes

#: Each path parameter that names an item, with the uncovered repository's items and items that do not exist.
ITEMS: dict[str, list[tuple[str, str]]] = {
    "proposal_id": [(PROPOSALS[UNCOVERED], "issue:no-such-1")],
    "decision_id": [
        (f"offer:{PROPOSALS[UNCOVERED]}", "offer:issue:no-such-1"),
        (f"done:{JOBS[UNCOVERED]}", "done:job-no-such"),
    ],
    "handoff_id": [("ho-gizmos", "ho-no-such")],
    "ask_id": [("ask-gizmos", "ask-no-such")],
}

#: A valid body for each route, so its answer comes from the route and not from validation.
BODIES: dict[str, dict[str, Any] | None] = {
    "/api/v1/proposals/{proposal_id}/accept": {"by": "ada"},
    "/api/v1/proposals/{proposal_id}/decline": {"by": "ada", "reason": "not now"},
    "/api/v1/proposals/{proposal_id}/snooze": {"by": "ada", "until": "2099-01-01T00:00:00Z"},
    "/api/v1/decisions/{decision_id}/send-to": {"to": "cy@example.com", "note": ""},
    "/api/v1/handoffs/{handoff_id}/return": {"note": ""},
    "/api/v1/handoffs/{handoff_id}/cancel": None,
    "/api/v1/asks/{ask_id}/sent": {"message_ref": "m-1"},
}

#: Write routes whose path names an item of another kind, and why the rule is not theirs.
NOT_ITEMS: dict[str, str] = {
    "/api/v1/repos/{full_repo:repo}/-/setup/proposal/{proposal_id}/apply": (
        "a repository's proposed test setup, addressed by the repository the caller names, which the allowlist "
        "refuses as every route naming a repository does"
    ),
    "/api/v1/repos/{full_repo:repo}/-/setup/proposal/{proposal_id}/reject": "as apply",
    "/api/v1/repos/{full_repo:repo}/setup/proposal/{proposal_id}/apply": "the old path of apply",
    "/api/v1/repos/{full_repo:repo}/setup/proposal/{proposal_id}/reject": "the old path of reject",
}

#: The press of a button in a chat names its decision by code, in the body: the uncovered one's and an unknown one.
ANSWER = "/api/v1/asks/answer"
CODES = ("GIZM", "QQQQ")
_PARAMETER = re.compile(r"\{(\w+)(?::\w+)?\}")


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    with offered_work_app(monkeypatch) as built:
        yield built


def item_routes(app: Any) -> dict[str, str]:
    """Each write route under ``/api/v1`` whose path names an item, with its method."""
    found: dict[str, str] = {}
    for route in app_routes(app):
        if not route.is_api_route or not route.path.startswith("/api/v1/") or route.path in NOT_ITEMS:
            continue
        for method in (route.methods or set()) - {"GET", "HEAD", "OPTIONS"}:
            if set(_PARAMETER.findall(route.path)) & set(ITEMS):
                found[route.path] = method
    return found


async def _call(app: Any, method: str, url: str, token: str, body: Any) -> Any:
    headers = {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        return await client.request(method, url, json=body)


def _answer(response: Any, item: str) -> tuple[int, Any]:
    """The status and body, with the item's own id, which a 404 may repeat whole or after its ``done:`` or
    ``offer:``, put back as ``<item>``."""
    text = response.text.replace(item, "<item>").replace(item.split(":", 1)[-1], "<item>")
    try:
        return response.status_code, json.loads(text)
    except ValueError:
        return response.status_code, text


async def acting_differently(app: Any) -> dict[str, str]:
    """Each item route, and the button press, whose answer to the limited key on the uncovered repository's item
    differs from its answer on a missing item, with how; and each listed route the key without an allowlist cannot
    reach the item through."""
    out: dict[str, str] = {}
    for path, method in sorted(item_routes(app).items()):
        if path not in BODIES:
            out[path] = "no body in BODIES: give the route a valid body"
            continue
        (name,) = set(_PARAMETER.findall(path)) & set(ITEMS)
        for uncovered_id, missing_id in ITEMS[name]:
            body = BODIES[path]
            uncovered = await _call(app, method, _PARAMETER.sub(uncovered_id, path), LIMITED, body)
            missing = await _call(app, method, _PARAMETER.sub(missing_id, path), LIMITED, body)
            if _answer(uncovered, uncovered_id) != _answer(missing, missing_id):
                out[f"{path} {uncovered_id}"] = (
                    f"{uncovered.status_code} {uncovered.text[:120]} against {missing.status_code} {missing.text[:120]}"
                )
            reached = await _call(app, method, _PARAMETER.sub(uncovered_id, path), OPEN, body)
            if _answer(reached, uncovered_id) == _answer(missing, missing_id):
                out[f"{path} {uncovered_id} (key without an allowlist)"] = (
                    f"did not reach the item: {reached.status_code} {reached.text[:120]}"
                )
    body = {"action": "decline", "person": "ada@example.com", "reason": "not now"}
    uncovered = await _call(app, "POST", ANSWER, LIMITED, {"code": CODES[0], **body})
    missing = await _call(app, "POST", ANSWER, LIMITED, {"code": CODES[1], **body})
    if _answer(uncovered, CODES[0]) != _answer(missing, CODES[1]):
        out[ANSWER] = (
            f"{uncovered.status_code} {uncovered.text[:120]} against {missing.status_code} {missing.text[:120]}"
        )
    reached = await _call(app, "POST", ANSWER, OPEN, {"code": CODES[0], **body})
    if _answer(reached, CODES[0]) == _answer(missing, CODES[1]):
        out[f"{ANSWER} (key without an allowlist)"] = (
            f"did not reach the decision: {reached.status_code} {reached.text[:120]}"
        )
    return out


@pytest.mark.asyncio
async def test_acting_on_an_item_a_key_does_not_cover_answers_as_acting_on_a_missing_one(app: Any) -> None:
    assert set(item_routes(app)) == set(BODIES), (
        f"the write routes naming an item are {sorted(item_routes(app))}; BODIES lists {sorted(BODIES)}"
    )
    differ = await acting_differently(app)
    assert differ == {}, (
        f"routes whose answer to a key acting on another repository's item is not their answer for a missing item: "
        f"{differ}. Read the item through the route's helper that answers as missing "
        "(embry0.api.repo_allowlist.hide_unless_covered), naming the action"
    )


@pytest.mark.asyncio
async def test_each_refusal_is_audited_though_the_answer_is_a_missing_item(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = AsyncMock()
    monkeypatch.setattr(repo_allowlist, "emit_audit", audit)
    for path, method in sorted(item_routes(app).items()):
        (name,) = set(_PARAMETER.findall(path)) & set(ITEMS)
        for uncovered_id, _missing in ITEMS[name]:
            audit.reset_mock()
            await _call(app, method, _PARAMETER.sub(uncovered_id, path), LIMITED, BODIES[path])
            refused = [c for c in audit.await_args_list if c.args[1] == "apikey.repo_refused"]
            assert len(refused) == 1 and refused[0].kwargs["details"]["repo"] == UNCOVERED, (
                f"{method} {path} on {uncovered_id}: the refusal is audited once, naming {UNCOVERED}: "
                f"{audit.await_args_list}"
            )
    audit.reset_mock()
    await _call(app, "POST", ANSWER, LIMITED, {"code": CODES[0], "action": "decline", "person": "ada@example.com"})
    assert [c.args[1] for c in audit.await_args_list] == ["apikey.repo_refused"], "and the button press's"


@pytest.mark.asyncio
async def test_the_inventory_finds_a_new_route_that_acts_on_an_item_without_the_rule(app: Any) -> None:
    """Not quiet by construction: a route that answers a proposal of any repository is reported."""
    from embry0.api.v1.proposals import _open

    extra = APIRouter()

    @extra.post("/proposals/{proposal_id}/nudge-for-a-test")
    async def nudge(proposal_id: str, request: Request) -> dict[str, Any]:
        return {"nudged": (await _open(request, proposal_id)).id}

    app.include_router(extra, prefix="/api/v1")
    BODIES["/api/v1/proposals/{proposal_id}/nudge-for-a-test"] = None
    try:
        differ = await acting_differently(app)
    finally:
        del BODIES["/api/v1/proposals/{proposal_id}/nudge-for-a-test"]
    assert list(differ) == [f"/api/v1/proposals/{{proposal_id}}/nudge-for-a-test {PROPOSALS[UNCOVERED]}"], differ
