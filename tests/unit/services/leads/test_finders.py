"""The finders (spec 2026-10-25): failing CI, flaky checks, alerts, unanswered reviews."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embry0.services.leads.alerts import find_alerts
from embry0.services.leads.ci import find_failing_checks
from embry0.services.leads.flaky import find_flaky_checks
from embry0.services.leads.reviews import find_unanswered_reviews
from tests.unit.services.leads.fakes import FakeForge

REPO = "acme/web"
HEAD = "a" * 40
BASE = {
    f"/repos/{REPO}": {"default_branch": "main"},
    f"/repos/{REPO}/commits/main": {"sha": HEAD, "commit": {"message": "Ship it\n\nbody"}},
}


def _run(name: str, conclusion: str, status: str = "completed", **extra: object) -> dict[str, object]:
    return {"name": name, "status": status, "conclusion": conclusion, "html_url": f"https://ci/{name}", **extra}


async def test_each_check_failing_on_the_default_branch_is_a_lead():
    client = FakeForge(
        {
            **BASE,
            f"/repos/{REPO}/commits/{HEAD}/check-runs": {
                "total_count": 4,
                "check_runs": [
                    _run("build", "failure", output={"title": "2 errors", "summary": "tsc: cannot find module"}),
                    _run("lint", "success"),
                    _run("e2e", "", status="in_progress"),
                    _run("deploy-preview", "timed_out"),
                ],
            },
            f"/repos/{REPO}/commits/{HEAD}/status": {
                "statuses": [
                    {"context": "coverage", "state": "failure", "target_url": "https://cov", "description": "71% < 80%"}
                ]
            },
        }
    )
    result = await find_failing_checks(client, REPO)
    assert result.complete
    by_name = {lead.data["check"]: lead for lead in result.leads}
    assert set(by_name) == {"build", "deploy-preview", "coverage"}
    build = by_name["build"]
    assert build.source_key == "check:build" and build.title == "build fails on main"
    assert build.severity == "high" and build.url == "https://ci/build" and "cannot find module" in build.details
    assert by_name["coverage"].data["source"] == "status"


async def test_an_unreadable_branch_is_skipped_and_a_partial_listing_closes_nothing():
    assert (await find_failing_checks(FakeForge({}), REPO)).skipped
    partial = FakeForge({**BASE, f"/repos/{REPO}/commits/{HEAD}/check-runs": {"total_count": 150, "check_runs": []}})
    assert not (await find_failing_checks(partial, REPO)).complete


async def test_a_check_that_failed_then_passed_on_one_commit_is_flaky():
    b = "b" * 40
    client = FakeForge(
        {
            **BASE,
            f"/repos/{REPO}/commits": [{"sha": HEAD}, {"sha": b}],
            f"/repos/{REPO}/commits/{HEAD}/check-runs": {
                "check_runs": [_run("unit", "failure"), _run("unit", "success")]
            },
            f"/repos/{REPO}/commits/{b}/check-runs": {
                "check_runs": [_run("unit", "failure"), _run("unit", "success"), _run("lint", "failure")]
            },
        }
    )
    result = await find_flaky_checks(client, REPO, commits=2)
    assert [lead.data["check"] for lead in result.leads] == ["unit"] and result.complete
    lead = result.leads[0]
    assert lead.data["shas"] == [HEAD, b] and "2 times in the last 2 commits" in lead.summary
    assert ("/repos/acme/web/commits/" + HEAD + "/check-runs", {"filter": "all", "per_page": 100}) in client.asked


async def test_alerts_from_both_sources_and_a_quiet_skip_when_neither_can_be_read():
    client = FakeForge(
        {
            f"/repos/{REPO}/dependabot/alerts": [
                {
                    "number": 7,
                    "html_url": "https://alerts/7",
                    "dependency": {
                        "package": {"name": "lodash", "ecosystem": "npm"},
                        "manifest_path": "web/package.json",
                    },
                    "security_advisory": {"ghsa_id": "GHSA-x", "summary": "Prototype pollution", "severity": "high"},
                    "security_vulnerability": {"severity": "high", "first_patched_version": {"identifier": "4.17.21"}},
                }
            ],
            f"/repos/{REPO}/code-scanning/alerts": (403, {"message": "Advanced Security must be enabled"}),
        }
    )
    result = await find_alerts(client, REPO)
    assert result.complete and not result.skipped
    (lead,) = result.leads
    assert lead.source_key == "dependabot:7" and lead.severity == "high" and lead.effort == "small"
    assert lead.paths == ("web/package.json",) and "4.17.21 fixes it" in lead.summary
    closed = await find_alerts(FakeForge({}), REPO)
    assert closed.skipped and not closed.leads


def _thread(reviewer: str, latest: str, days_ago: int, **flags: object) -> dict[str, object]:
    when = (datetime(2026, 10, 25, tzinfo=UTC) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "isResolved": flags.get("resolved", False),
        "isOutdated": flags.get("outdated", False),
        "path": "src/app.py",
        "line": 10,
        "opened": {
            "nodes": [{"author": {"login": reviewer}, "body": "Please handle the empty case", "createdAt": when}]
        },
        "latest": {"nodes": [{"author": {"login": latest}, "createdAt": when}]},
    }


async def test_review_threads_a_reviewer_started_and_nobody_answered_make_a_lead():
    pull = {
        "number": 42,
        "title": "Add export",
        "url": "https://forge/pull/42",
        "isDraft": False,
        "isCrossRepository": False,
        "headRefName": "feat/export",
        "author": {"login": "dev"},
        "reviewThreads": {
            "nodes": [
                _thread("rev", "rev", 5),
                _thread("rev", "dev", 5),
                _thread("rev", "rev", 1),
                _thread("rev", "rev", 5, resolved=True),
                _thread("dev", "dev", 5),
            ]
        },
    }
    draft = {**pull, "number": 43, "isDraft": True}
    fork = {**pull, "number": 44, "isCrossRepository": True}
    body = {"data": {"repository": {"pullRequests": {"totalCount": 3, "nodes": [pull, draft, fork]}}}}
    now = datetime(2026, 10, 25, tzinfo=UTC)
    result = await find_unanswered_reviews(FakeForge(graphql=body), REPO, now=now, stale_days=3)
    (lead,) = result.leads
    assert result.complete and lead.source_key == "pr:42" and lead.data["head_ref"] == "feat/export"
    assert len(lead.data["threads"]) == 1 and lead.summary.startswith("1 review thread waits for an answer on #42")
    errors = await find_unanswered_reviews(FakeForge(graphql={"errors": [{"message": "no"}]}), REPO, now=now)
    assert not errors.complete and not errors.leads
