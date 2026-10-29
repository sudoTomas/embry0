"""A job's ``context.ref`` is checked at the API with the git initializer's own rule.

The ref reaches the sandbox's clone line. A ref the initializer would refuse
used to be stored and fail the job at init; it is now a 422 on create.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from embry0.api.app import create_app
from embry0.api.schemas import ContextType, JobContext, JobCreateRequest
from embry0.config import Embry0Config


@pytest.mark.parametrize("ref", ["main", "release/2026.09", "embry0/iss-abc-fix", "a" * 40, "v1.2.3", ""])
def test_a_ref_the_clone_takes_is_accepted(ref: str) -> None:
    context = JobContext(type=ContextType.git, repo="acme/widgets", ref=ref)
    assert context.ref == ref, f"a ref the clone takes must be kept as sent: {context.ref!r}"


@pytest.mark.parametrize("ref", ["main\n", "-x", "feat branch", "a;b", "a$b", pytest.param("x" * 256, id="256 chars")])
def test_a_ref_the_clone_refuses_is_refused(ref: str) -> None:
    try:
        JobCreateRequest(task="t", context={"type": "git", "repo": "acme/widgets", "ref": ref})
    except ValidationError as exc:
        assert "context.ref must be a branch, tag or sha" in str(exc), f"the refusal must name context.ref: {exc}"
    else:
        raise AssertionError(f"a ref the clone refuses must be refused at the API: {ref!r}")


def _app() -> Any:
    @asynccontextmanager
    async def _noop(_app: Any) -> Any:
        yield

    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config, lifespan_override=_noop)
    app.state.config = config
    app.state.jobs_repo = object()  # get_jobs_repo resolves with the body; a refused body never uses it
    return app


@pytest.mark.parametrize("ref", ["main\n", "-x"])
async def test_creating_a_job_with_a_ref_the_clone_refuses_is_a_422(ref: str) -> None:
    body = {"task": "t", "context": {"type": "git", "repo": "acme/widgets", "ref": ref}}
    # A ref that passes reaches the handler, which this bare app cannot serve: a 500, not an exception.
    transport = ASGITransport(app=_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs", json=body, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 422, f"a ref the clone refuses must be a 422: {resp.status_code} {resp.text}"
    assert "context.ref" in resp.text, f"the 422 must name context.ref: {resp.text}"
