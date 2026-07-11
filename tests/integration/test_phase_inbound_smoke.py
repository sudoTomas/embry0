"""Plan B Task 7 — end-to-end inbound smoke.

GitHub posts an ``issue_comment`` containing a ``/answer N: <text>`` directive
to the webhook. The webhook handler in ``embry0/api/v1/webhooks.py`` should:

  parse the directive
    → look up the matching pending ``issue_inputs`` row by sequence
    → call ``inputs_repo.answer(...)`` (status flips to ``answered``,
      ``answer`` column populated, ``answered_by`` recorded as ``github:<login>``)
    → call ``executor.resume_for_issue(issue_id)`` once no blocking inputs
      remain (this is a no-op here because we don't seed a real
      ``awaiting_input`` job — the resume-pipeline path is exercised by
      Plan A Task 8's ``test_phase_quickwins_smoke``; this test's primary
      goal is verifying the inbound webhook persists answers correctly).

The integration ``app`` fixture in ``conftest.py`` skips when Postgres is
unavailable. The fixture configures ``webhook_dev_mode=True`` AND a
non-empty ``github_webhook_secret="test-secret"`` — that combination
*requires* a valid HMAC signature (dev_mode only bypasses when the secret
is empty, per ``embry0.api.auth.verify_webhook_signature``). So the
test computes a real ``sha256=...`` over the JSON-encoded body using the
known fixture secret rather than patching ``verify_webhook_signature``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid

import pytest
from httpx import AsyncClient

CSRF: dict[str, str] = {"X-Requested-With": "XMLHttpRequest"}

# Must match ``conftest.app`` fixture's ``github_webhook_secret``.
_FIXTURE_WEBHOOK_SECRET = "test-secret"


def _sign(body: bytes, secret: str = _FIXTURE_WEBHOOK_SECRET) -> str:
    """Compute the GitHub-style ``X-Hub-Signature-256`` over a body."""
    digest = hmac.HMAC(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.2) -> bool:
    """Poll ``predicate()`` (async) until True or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_github_webhook_answer_directive_applies_to_issue_input(
    app: AsyncClient,
) -> None:
    """End-to-end: GitHub webhook ``/answer 1: <text>`` updates the
    matching ``issue_inputs`` row to ``answered`` and writes the answer
    text + ``answered_by=github:<login>`` to the DB.

    Setup is done via the live DB pool reachable through the app's ASGI
    transport (matches the ``qa_minio_seeded`` fixture pattern). Cleanup
    runs in a finally block so reruns don't leak rows.
    """
    # The httpx AsyncClient holds the FastAPI app on its transport.
    # ``conftest._make_test_lifespan`` populates ``app.state.db`` via
    # ``_init_app_state`` so the integration test can issue raw SQL.
    fastapi_app = app._transport.app  # noqa: SLF001
    db = fastapi_app.state.db

    # Use a unique github_number per run to avoid colliding with the
    # partial unique index ``idx_issues_github (repo, github_number)``
    # if cleanup ever fails to fire (best-effort safety).
    suffix = uuid.uuid4().hex[:8]
    issue_id = f"iss-inbound-smoke-{suffix}"
    job_id = f"job-inbound-smoke-{suffix}"
    input_id = f"inp-inbound-smoke-{suffix}"
    repo_full = "owner/inbound-smoke"
    github_number = 90000 + (int(suffix, 16) % 9000)

    try:
        # Seed: an issue in ``awaiting_input`` with one pending blocking input.
        # job row is required because ``issue_inputs.job_id`` is NOT NULL with a
        # FK to ``jobs(job_id)``. The job stays in ``pending`` (default) — that
        # makes ``executor.resume_for_issue`` a no-op (it only resumes
        # ``awaiting_input`` jobs), which is fine: this test verifies the
        # inbound persistence path, not the LangGraph resume.
        await db.execute(
            """
            INSERT INTO issues (id, repo, title, body, status, github_number, github_sync_enabled)
            VALUES ($1, $2, 't', 'b', 'awaiting_input', $3, TRUE)
            """,
            issue_id,
            repo_full,
            github_number,
        )
        await db.execute(
            """
            INSERT INTO jobs (job_id, repo, task, issue_id, status)
            VALUES ($1, $2, 'inbound-smoke', $3, 'pending')
            """,
            job_id,
            repo_full,
            issue_id,
        )
        await db.execute(
            """
            INSERT INTO issue_inputs (id, issue_id, job_id, asking_node, question, importance, status)
            VALUES ($1, $2, $3, 'developer', 'Q1?', 'blocking', 'pending')
            """,
            input_id,
            issue_id,
            job_id,
        )

        # POST the GitHub issue_comment webhook payload, signed with the
        # fixture's webhook secret. Encode the body explicitly so the signature
        # is computed over the exact bytes the server sees.
        payload = {
            "action": "created",
            "comment": {
                "body": "/answer 1: yes from github webhook",
                "user": {"login": "tester"},
            },
            "issue": {"number": github_number},
            "repository": {"full_name": repo_full},
        }
        body_bytes = json.dumps(payload).encode()
        # Note: the route is ``/api/v1/webhook`` (singular), not /webhooks/github.
        r = await app.post(
            "/api/v1/webhook",
            content=body_bytes,
            headers={
                "X-GitHub-Event": "issue_comment",
                "X-Hub-Signature-256": _sign(body_bytes),
                "Content-Type": "application/json",
                **CSRF,
            },
        )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("applied") == 1, body
        assert body.get("skipped", 0) == 0, body
        assert body.get("unmatched", 0) == 0, body

        # The dispatcher's answer call is awaited synchronously inside the
        # webhook handler, but ``resume_for_issue`` background-tracks the
        # actual resume. The DB row update from ``inputs_repo.answer`` is
        # synchronous within the request, so a single poll should suffice;
        # use a short polling window for safety against scheduler jitter.
        async def _input_answered() -> bool:
            row = await db.fetchrow(
                "SELECT status, answer, answered_by FROM issue_inputs WHERE id = $1",
                input_id,
            )
            return row is not None and row["status"] == "answered"

        flipped = await _wait_until(_input_answered, timeout=5.0, interval=0.2)
        assert flipped, "issue_inputs row did not flip to 'answered' within 5s"

        row = await db.fetchrow(
            "SELECT status, answer, answered_by FROM issue_inputs WHERE id = $1",
            input_id,
        )
        assert row is not None
        assert row["status"] == "answered"
        assert row["answer"] == "yes from github webhook"
        assert row["answered_by"] == "github:tester"
    finally:
        # Best-effort cleanup so reruns don't leak rows. Order matters:
        # issue_inputs → jobs (jobs FK) → issues (issues FK on jobs).
        try:
            await db.execute("DELETE FROM issue_inputs WHERE id = $1", input_id)
        except Exception:
            pass
        try:
            await db.execute("DELETE FROM jobs WHERE job_id = $1", job_id)
        except Exception:
            pass
        try:
            await db.execute("DELETE FROM issues WHERE id = $1", issue_id)
        except Exception:
            pass
