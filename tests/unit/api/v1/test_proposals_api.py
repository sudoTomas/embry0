"""Owner proposals API (spec 2026-10-24): the feed with its owners, and each answer through its kind's own path."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from embry0.api.v1 import proposals as proposals_api
from embry0.services.proposals import Decision

T0 = datetime(2026, 10, 24, 12, tzinfo=UTC)
REPORT = "## Findings\n\n| Severity | Location | Claim | Evidence | Fix |\n|---|---|---|---|---|\n| misleads | AGENTS.md:3 | Run make check | no target | say make test |\n"


class FakeRepo:
    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []
        self.answers: dict[str, Decision] = {}

    async def cases(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "hc-1",
                "repo": "acme/api",
                "title": "Timeouts",
                "severity": "high",
                "occurrence_count": 3,
                "created_at": T0,
                "tracked_at": T0,
            }
        ]

    async def issues(self) -> list[dict[str, Any]]:
        return [{"id": "iss-1", "repo": "acme/web", "title": "Fix login", "created_at": T0 + timedelta(hours=1)}]

    async def audits(self) -> list[dict[str, Any]]:
        return [
            {"repo": "acme/docs", "job_id": "job-a", "finished_at": T0 - timedelta(hours=1), "result_summary": REPORT}
        ]

    async def owners(self) -> dict[str, list[str]]:
        return {"acme/api": ["octo"], "acme/web": ["octo", "pat"]}

    async def decisions(self) -> dict[str, Decision]:
        return self.answers

    async def record(self, **row: Any) -> None:
        self.recorded.append(row)
        self.answers[row["proposal_id"]] = Decision(
            row["proposal_id"], row["decision"], row["decided_by"], datetime.now(UTC), row.get("until")
        )


class FakeForge:
    async def get(self, path: str, **_: Any) -> Any:
        if path == "/repos/acme/docs/contents/.github/CODEOWNERS":
            body = {"content": base64.b64encode(b"* @docs-owner @acme/team\n").decode()}
            return SimpleNamespace(status_code=200, json=lambda: body)
        return SimpleNamespace(status_code=404, json=lambda: {})


class FakeOrgs:
    async def get(self, owner: str) -> Any:
        return SimpleNamespace(owner=owner) if owner == "acme" else None


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(proposals_api.router, prefix="/api/v1")
    app.state.proposals_repo = FakeRepo()
    app.state.organizations_repo = FakeOrgs()
    app.state.config = SimpleNamespace(audit_log_path=None)
    app.state.db = None

    async def forge_client(request: Any, org: Any) -> FakeForge:
        return FakeForge()

    monkeypatch.setattr("embry0.api.v1.organizations.org_forge_client", forge_client)

    async def no_audit(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(proposals_api, "emit_audit", no_audit)
    return app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_the_feed_lists_every_kind_newest_first_with_its_owners(app: FastAPI) -> None:
    async with await _client(app) as client:
        body = (await client.get("/api/v1/proposals")).json()
    assert [p["kind"] for p in body["proposals"]] == ["issue", "case", "finding"]
    owners = {p["repo"]: p["owners"] for p in body["proposals"]}
    assert owners == {"acme/web": ["octo", "pat"], "acme/api": ["octo"], "acme/docs": ["docs-owner"]}, (
        "preferences first, else CODEOWNERS (teams skipped)"
    )
    finding = body["proposals"][2]
    assert finding["id"].startswith("finding:") and finding["path"] == "AGENTS.md" and finding["line"] == 3


async def test_the_feed_filters_by_owner_and_kind(app: FastAPI) -> None:
    async with await _client(app) as client:
        mine = (await client.get("/api/v1/proposals", params={"owner": "@Octo"})).json()
        cases = (await client.get("/api/v1/proposals", params={"kind": "case"})).json()
        bad = await client.get("/api/v1/proposals", params={"kind": "merge"})
    assert sorted(p["id"] for p in mine["proposals"]) == ["case:hc-1", "issue:iss-1"]
    assert [p["id"] for p in cases["proposals"]] == ["case:hc-1"]
    assert bad.status_code == 422


async def test_accept_runs_each_kind_through_its_own_path(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def accept_case(case_id: str, body: Any, request: Any, cases: Any) -> dict[str, Any]:
        calls.append(("case", f"{case_id} by {body.accepted_by}"))
        return {"job_id": "job-case"}

    async def triage_issue(issue_id: str, request: Any, issues: Any, executor: Any, body: Any = None) -> Any:
        calls.append(("issue", issue_id))
        return SimpleNamespace(job_id="job-issue")

    async def start_fix(request: Any, proposal: Any, run_with: Any = None) -> str:
        calls.append(("finding", proposal.extra["path"]))
        return "job-fix"

    monkeypatch.setattr("embry0.api.v1.cases.accept_case", accept_case)
    monkeypatch.setattr("embry0.api.v1.issues.triage_issue", triage_issue)
    monkeypatch.setattr(proposals_api, "_start_fix", start_fix)
    monkeypatch.setattr(proposals_api, "get_healing_cases_repo", lambda request: None)
    monkeypatch.setattr(proposals_api, "get_issues_repo", lambda request: None)
    monkeypatch.setattr(proposals_api, "get_issue_executor", lambda request: None)

    async with await _client(app) as client:
        finding_id = next(
            p["id"] for p in (await client.get("/api/v1/proposals")).json()["proposals"] if p["kind"] == "finding"
        )
        answers = [
            await client.post(f"/api/v1/proposals/{pid}/accept", json={"by": "octo"})
            for pid in ("case:hc-1", "issue:iss-1", finding_id)
        ]
        after = (await client.get("/api/v1/proposals")).json()
        again = await client.post("/api/v1/proposals/case:hc-1/accept", json={"by": "octo"})
    assert [a.status_code for a in answers] == [202, 202, 202]
    assert [a.json()["job_id"] for a in answers] == ["job-case", "job-issue", "job-fix"]
    assert calls == [("case", "hc-1 by octo"), ("issue", "iss-1"), ("finding", "AGENTS.md")]
    assert after["count"] == 0, "an accepted proposal is not asked again"
    assert again.status_code == 404


async def test_a_failed_start_records_no_answer(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    async def refuse(issue_id: str, request: Any, issues: Any, executor: Any, body: Any = None) -> Any:
        raise HTTPException(status_code=409, detail="Cannot triage issue in 'triaging' state")

    monkeypatch.setattr("embry0.api.v1.issues.triage_issue", refuse)
    for name in ("get_issues_repo", "get_issue_executor"):
        monkeypatch.setattr(proposals_api, name, lambda request: None)
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/issue:iss-1/accept", json={"by": "octo"})
    assert response.status_code == 409 and app.state.proposals_repo.recorded == []


async def test_decline_and_snooze_record_who_and_why(app: FastAPI) -> None:
    async with await _client(app) as client:
        declined = await client.post(
            "/api/v1/proposals/case:hc-1/decline", json={"by": "octo", "reason": "known, harmless"}
        )
        past = await client.post(
            "/api/v1/proposals/issue:iss-1/snooze", json={"by": "pat", "until": (T0 - timedelta(days=1)).isoformat()}
        )
        until = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        snoozed = await client.post("/api/v1/proposals/issue:iss-1/snooze", json={"by": "pat", "until": until})
        left = (await client.get("/api/v1/proposals")).json()
    assert declined.status_code == 200 and declined.json()["decision"] == "decline"
    assert past.status_code == 422
    assert snoozed.status_code == 200 and snoozed.json()["until"] is not None
    assert [p["kind"] for p in left["proposals"]] == ["finding"]
    recorded = app.state.proposals_repo.recorded
    assert [(r["proposal_id"], r["decision"], r["decided_by"], r["reason"]) for r in recorded] == [
        ("case:hc-1", "decline", "octo", "known, harmless"),
        ("issue:iss-1", "snooze", "pat", ""),
    ]


async def test_an_answer_must_say_who_gave_it(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.post("/api/v1/proposals/case:hc-1/decline", json={"reason": "no"})
    assert response.status_code == 422


async def test_a_finding_fix_is_an_audit_job_on_its_one_file_that_opens_a_pull_request(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: dict[str, Any] = {}

    async def read_head(client: Any, repo: str) -> tuple[str, str]:
        return "abc123", "tree1"

    async def start_job(context: Any, **kwargs: Any) -> str:
        started.update(context=context, **kwargs)
        return "job-fix"

    monkeypatch.setattr("embry0.services.context_audit.read_head", read_head)
    monkeypatch.setattr("embry0.services.context_audit.start_context_audit_job", start_job)
    refusal: list[Any] = [None]
    app.state.issue_executor = SimpleNamespace(dispatch_refusal=lambda actor: _resolved(refusal[0]))
    app.state.jobs_repo = app.state.templates_repo = object()
    app.state.config = SimpleNamespace(audit_log_path=None, context_audit_job_budget_usd=2.0)

    async with await _client(app) as client:
        finding_id = next(
            p["id"] for p in (await client.get("/api/v1/proposals")).json()["proposals"] if p["kind"] == "finding"
        )
        refusal[0] = SimpleNamespace(public_message="the daily budget is spent")
        refused = await client.post(f"/api/v1/proposals/{finding_id}/accept", json={"by": "docs-owner"})
        refusal[0] = None
        accepted = await client.post(f"/api/v1/proposals/{finding_id}/accept", json={"by": "docs-owner"})
    assert refused.status_code == 429 and "budget" in refused.json()["detail"]
    assert accepted.status_code == 202 and accepted.json()["job_id"] == "job-fix"
    assert started["context"].files == ("AGENTS.md",) and started["context"].head_sha == "abc123"
    assert started["open_pr"] is True and started["dispatched_by"] == "embry0:proposals"
    assert "The claim at AGENTS.md:3: Run make check" in started["task"]


async def _resolved(value: Any) -> Any:
    return value
