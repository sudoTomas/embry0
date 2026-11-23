"""An action on a job's pull request acts only on the job's own repository, whatever key asks.

A job's ``pr_url`` is what its agent's output named. Sending the job back and closing its pull request read the
repository from that address, so each first checks it is the job's, on the forge the job's repository is registered
on: a key limited to the job's repository, and a key with no allowlist, both get 409 and nothing reaches the forge.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres


@pytest.mark.parametrize(
    ("key", "pr_url", "forge"),
    [
        ("limited", f"https://github.com/{UNCOVERED}/pull/5", "github"),
        ("open", f"https://github.com/{UNCOVERED}/pull/5", "github"),
        ("open", f"https://github.com/{COVERED}/pull/5", "gitlab"),
    ],
    ids=["another-repository-limited-key", "another-repository-open-key", "same-path-on-another-forge"],
)
async def test_a_job_s_pull_request_in_another_repository_is_not_acted_on(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, key: str, pr_url: str, forge: str
) -> None:
    from embry0.api.middleware.rate_limit import RateLimitMiddleware
    from embry0.api.v1 import organizations as organizations_api

    monkeypatch.setattr(RateLimitMiddleware, "_over_limit", lambda _s, _k, _m: False)
    writes: list[tuple[str, str]] = []

    class Forge:
        async def post(self, path: str, *, repo: str, json: dict[str, Any], **_: Any) -> Any:
            writes.append(("POST", path))
            return SimpleNamespace(status_code=201)

        async def patch(self, path: str, *, repo: str, json: dict[str, Any], **_: Any) -> Any:
            writes.append(("PATCH", path))
            return SimpleNamespace(status_code=200)

    async def org_forge_client(_request: Any, org: Any) -> Forge:
        return Forge()

    monkeypatch.setattr(organizations_api, "org_forge_client", org_forge_client)
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, values):
        job_id = values[COVERED]["job_id"]
        await db_with_migrations.execute(
            "UPDATE jobs SET pr_url = $2, delivery_state = 'pr_open', status = 'completed' WHERE job_id = $1",
            job_id,
            pr_url,
        )
        # The forge the job's repository is registered on.
        await db_with_migrations.execute(
            "INSERT INTO repos (repo, forge) VALUES ($1, $2) ON CONFLICT (repo) DO UPDATE SET forge = EXCLUDED.forge",
            COVERED,
            forge,
        )
        token = getattr(keys, key)
        closing = await call(app, "POST", f"/api/v1/jobs/{job_id}/reject", token, {"note": "no", "close_pr": True})
        sent = await call(app, "POST", f"/api/v1/jobs/{job_id}/send-back", token, {"notes": "again"})
        rated = await db_with_migrations.fetchval("SELECT count(*) FROM job_ratings WHERE job_id = $1", job_id)
    assert (closing.status_code, sent.status_code) == (409, 409), (closing.text, sent.text)
    assert (writes, rated) == ([], 0), f"the forge was written or the rejection recorded: {writes}, {rated}"
