"""Embry0Client's review methods: each calls the job's own review route, with only what the caller chose."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import httpx
import pytest

from embry0.mcp_server.client import Embry0Client

#: The client's review methods: the evaluation's read and the five review answers.
METHODS = ("job_evaluation", "job_evaluate", "job_send_back", "job_run_again", "job_dismiss", "job_reject")


def _client(seen: list[httpx.Request]) -> Embry0Client:
    missing = [name for name in METHODS if not hasattr(Embry0Client, name)]
    assert missing == [], f"the MCP client cannot review a job: it has no {missing}"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"job_id": "job-1"})

    return Embry0Client("http://embry0.test", "test-key", transport=httpx.MockTransport(handler))


def _body(request: httpx.Request) -> object:
    return json.loads(request.content) if request.content else None


async def test_each_review_method_calls_the_job_s_review_route() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    await client.job_evaluation("job-1")
    await client.job_evaluate("job-1", rating="good", note="clean")
    await client.job_send_back("job-1", "Handle the empty list too.")
    await client.job_run_again("job-1")
    await client.job_dismiss("job-1")
    await client.job_reject("job-1", note="not wanted", close_pr=True)
    await client.aclose()
    assert [(r.method, r.url.path, _body(r)) for r in seen] == [
        ("GET", "/api/v1/jobs/job-1/evaluation", None),
        ("PUT", "/api/v1/jobs/job-1/evaluation", {"rating": "good", "note": "clean"}),
        ("POST", "/api/v1/jobs/job-1/send-back", {"notes": "Handle the empty list too."}),
        ("POST", "/api/v1/jobs/job-1/run-again", {}),
        ("POST", "/api/v1/jobs/job-1/dismiss", None),
        ("POST", "/api/v1/jobs/job-1/reject", {"note": "not wanted", "close_pr": True}),
    ], "each review method calls /jobs/{job_id}/…, never the deprecated world paths, and sends what was chosen"


async def test_an_evaluation_sends_only_the_parts_given() -> None:
    """The route refuses an evaluation with nothing in it (422), so an empty part is left out, never sent as ''."""
    seen: list[httpx.Request] = []
    client = _client(seen)
    await client.job_evaluate("job-1", verdict="merged")
    await client.job_evaluate("job-1")
    await client.aclose()
    assert [_body(r) for r in seen] == [{"verdict": "merged"}, {}], [r.content for r in seen]


async def test_run_with_is_sent_only_when_chosen() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)
    await client.job_send_back("job-1", "again", model="m-1")
    await client.job_run_again("job-1", credential_id="cred-000000000009")
    await client.job_run_again("job-1", model="m-1", credential_id="cred-000000000009")
    await client.aclose()
    assert [_body(r) for r in seen] == [
        {"notes": "again", "run_with": {"model": "m-1"}},
        {"run_with": {"credential_id": "cred-000000000009"}},
        {"run_with": {"model": "m-1", "credential_id": "cred-000000000009"}},
    ]


#: Each review method called on ``job``: the read, then the five writes.
CALLS: dict[str, Callable[[Embry0Client, str], Awaitable[object]]] = {
    "job_evaluation": lambda c, job: c.job_evaluation(job),
    "job_evaluate": lambda c, job: c.job_evaluate(job, rating="good"),
    "job_send_back": lambda c, job: c.job_send_back(job, "again"),
    "job_run_again": lambda c, job: c.job_run_again(job),
    "job_dismiss": lambda c, job: c.job_dismiss(job),
    "job_reject": lambda c, job: c.job_reject(job),
}


@pytest.mark.parametrize("method", list(CALLS))
async def test_a_job_id_stays_one_path_segment(method: str) -> None:
    """An id that is not a job's cannot reach another route: it is quoted into one segment."""
    assert set(CALLS) == set(METHODS), "every review method is called here"
    seen: list[httpx.Request] = []
    client = _client(seen)
    for job in ("../cancel", "job-1/cancel?x=1"):
        await CALLS[method](client, job)
    await client.aclose()
    paths = [r.url.raw_path for r in seen]
    assert [path.split(b"/")[4] for path in paths] == [b"..%2Fcancel", b"job-1%2Fcancel%3Fx%3D1"], (
        f"{method}: a job id left its path segment: {paths}"
    )
    assert all(path.startswith(b"/api/v1/jobs/") and path.count(b"/") == 5 for path in paths), (
        f"{method}: the request reached another route: {paths}"
    )
