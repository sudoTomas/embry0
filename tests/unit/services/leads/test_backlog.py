"""The backlog intake (spec 2026-10-25): which tickets read as ready, and which are left to other paths."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from embry0.execution.forge_client import Listing
from embry0.services.leads import Lead, lead_id
from embry0.services.leads.backlog import (
    effort_of,
    find_github_tickets,
    linear_leads,
    paths_in,
    readiness,
    severity_of,
    ticket_lead,
)
from tests.unit.services.leads.fakes import FakeForge

READY_BODY = (
    "The export fails with `ValueError: invalid date` when the start date is empty.\n"
    "It happens in services/export/report.py when the form posts no date.\n\n"
    "Expected behavior: an empty start date exports everything.\n"
    "- [ ] export with no start date works\n"
)


async def _untracked(tracker: str, repo: str, key: str) -> bool:
    return False


def test_a_ticket_with_files_an_error_and_a_done_line_reads_as_ready():
    score, why = readiness("Export fails on an empty start date", READY_BODY, ["bug"])
    assert score >= 60, (score, why)
    assert {"names files", "quotes an error", "says what done looks like", "labelled bug"} <= set(why)


@pytest.mark.parametrize(
    ("title", "body", "labels", "estimate"),
    [
        ("Rethink billing", "", ["epic"], None),
        ("Idea", "maybe we should", [], None),
        ("Redesign the onboarding flow end to end", "Big piece of work. " * 30, ["needs design"], 8),
    ],
)
def test_epics_questions_and_one_liners_do_not(title, body, labels, estimate):
    score, _ = readiness(title, body, labels, estimate)
    assert score < 40


@pytest.mark.parametrize(
    "title", ["Epic: deploys like the big platforms", "[EPIC] Billing rework", "epic(roost): plan"]
)
def test_an_epic_by_its_title_alone_does_not_read_as_ready(title):
    """A planning ticket names itself an epic in its title as often as by a label."""
    score, why = readiness(title, READY_BODY, ["bug"])
    assert "an epic by its title" in why
    assert score < readiness("Export fails on an empty start date", READY_BODY, ["bug"])[0] - 25


def test_a_title_that_only_mentions_epic_is_judged_as_usual():
    assert "an epic by its title" not in readiness("fix(leads): epic filter drops contacts", READY_BODY, ["bug"])[1]


def test_paths_effort_and_severity():
    assert paths_in("see `services/export/report.py:12` and app.ts, not v1.2") == (
        "services/export/report.py",
        "app.ts",
    )
    assert effort_of(["size:s"], None, READY_BODY) == "small"
    assert effort_of([], 8, "") == "large"
    assert effort_of([], None, READY_BODY) == "medium"
    assert severity_of([], 1) == "critical" and severity_of([], 4) == "low"
    assert severity_of(["security"]) == "critical" and severity_of(["bug"]) == "medium"


def test_a_ticket_lead_says_why_it_is_offered_and_how_it_starts():
    lead = ticket_lead(
        repo="acme/web",
        tracker="linear",
        key="ENG-12",
        title="Export fails on an empty start date",
        body=READY_BODY,
        url="https://tracker/ENG-12",
        labels=["bug"],
        priority=2,
    )
    assert isinstance(lead, Lead)
    assert lead.id == lead_id("ticket", "acme/web", "linear:ENG-12")
    assert lead.severity == "high" and lead.paths == ("services/export/report.py",)
    assert lead.summary.startswith("A backlog ticket embry0 could take (")
    assert lead.action == "Put the trigger label on the ticket and start its pipeline"
    assert lead.data == {"tracker": "linear", "key": "ENG-12", "readiness": lead.data["readiness"]}
    assert ticket_lead(repo="acme/web", tracker="github", key="3", title="Idea", body="", url=None, labels=[]) is None


def _ticket(key: str, *, repo: str = "acme/web", labels: tuple[str, ...] = ("bug",), assigned=False, mine=False):
    issue = SimpleNamespace(
        key=key, title=f"Fix the export for {key}", body=READY_BODY, url=f"https://t/{key}", labels=list(labels)
    )
    return SimpleNamespace(issue=issue, repo=repo, assigned=assigned, mine=mine, priority=0, estimate=None)


async def test_linear_tickets_someone_holds_labelled_or_tracked_are_left_alone():
    async def tracked(tracker: str, repo: str, key: str) -> bool:
        return key == "ENG-4"

    tickets = [
        _ticket("ENG-1"),
        _ticket("ENG-2", repo="acme/api"),
        _ticket("ENG-3", assigned=True),
        _ticket("ENG-4"),
        _ticket("ENG-5", labels=("bug", "Embry0")),
        _ticket("ENG-6", assigned=True, mine=True),
    ]
    leads = await linear_leads("acme/web", tickets, trigger_labels=["embry0"], tracked=tracked)
    assert [lead.data["key"] for lead in leads] == ["ENG-1", "ENG-6"]


async def test_github_issues_nobody_holds_and_not_yet_tracked_become_leads():
    issues = [
        {
            "number": 1,
            "title": "Export fails on an empty start date",
            "body": READY_BODY,
            "labels": [{"name": "bug"}],
            "html_url": "u1",
        },
        {"number": 2, "title": "A pull request", "body": READY_BODY, "pull_request": {}, "labels": []},
        {"number": 3, "title": "Export fails again somewhere", "body": READY_BODY, "assignees": [{"login": "octo"}]},
        {"number": 4, "title": "Export fails for embry0 already", "body": READY_BODY, "labels": [{"name": "embry0"}]},
        {"number": 5, "title": "Rethink billing", "body": "", "labels": [{"name": "epic"}]},
    ]
    client = FakeForge({"/repos/acme/web/issues": Listing(issues, complete=True)})
    result = await find_github_tickets(client, "acme/web", trigger_labels=["embry0"], tracked=_untracked)
    assert [lead.data["key"] for lead in result.leads] == ["1"] and result.complete
    partial = FakeForge({"/repos/acme/web/issues": Listing(issues, complete=False)})
    assert not (await find_github_tickets(partial, "acme/web", trigger_labels=[], tracked=_untracked)).complete
