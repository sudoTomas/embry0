"""The backlog intake (spec 2026-10-25): which tickets read as ready, and which are left to other paths.

Each ticket is read with the ``leads`` section's default vocabulary here;
``test_readiness.py`` reads it in an organization's own words.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.execution.forge_client import Listing
from embry0.services.leads import Lead, lead_id
from embry0.services.leads.backlog import backlog_leads, paths_in, ticket_lead
from embry0.services.leads.readiness import Readiness
from embry0.settings.sections.leads import LeadsSettings
from tests.unit.services.leads.fakes import FakeForge, linear_tracker

#: The reading the default section gives: the vocabulary the intake held in its code before #168.
DEFAULT = Readiness.of(LeadsSettings())
readiness = DEFAULT.score
effort_of = DEFAULT.effort
severity_of = DEFAULT.severity

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
        min_score=40,
        readiness=DEFAULT,
    )
    assert isinstance(lead, Lead)
    assert lead.id == lead_id("ticket", "acme/web", "linear:ENG-12")
    assert lead.severity == "high" and lead.paths == ("services/export/report.py",)
    assert lead.summary.startswith("A backlog ticket embry0 could take (")
    assert lead.action == "Put the trigger label on the ticket and start its pipeline"
    assert lead.data == {"tracker": "linear", "key": "ENG-12", "readiness": lead.data["readiness"]}
    idea = ticket_lead(
        repo="acme/web",
        tracker="github",
        key="3",
        title="Idea",
        body="",
        url=None,
        labels=[],
        min_score=40,
        readiness=DEFAULT,
    )
    assert idea is None


def _ticket(key: str, *, repo: str = "acme/web", labels: tuple[str, ...] = ("bug",), assigned=False, mine=False):
    issue = SimpleNamespace(
        key=key, title=f"Fix the export for {key}", body=READY_BODY, url=f"https://t/{key}", labels=list(labels)
    )
    return SimpleNamespace(issue=issue, repo=repo, assigned=assigned, mine=mine, priority=0, estimate=None)


def _linear_tracker(tickets: list[Any]) -> Any:
    async def list_backlog_tickets() -> tuple[list[Any], bool]:
        return tickets, True

    return linear_tracker(SimpleNamespace(list_backlog_tickets=list_backlog_tickets))


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
    read = await _linear_tracker(tickets).list_backlog(["ACME/web"])
    assert read.whole == frozenset({"ACME/web"}), "Linear is read whole for every repository it was asked about"
    assert {item.repo for item in read.items} == {"acme/web"}, "an item keeps Linear's name for its repository"
    leads = await backlog_leads(read.items, trigger_labels=["embry0"], tracked=tracked, min_score=40, readiness=DEFAULT)
    assert [lead.data["key"] for lead in leads] == ["ENG-1", "ENG-6"]


async def test_github_issues_nobody_holds_and_not_yet_tracked_become_leads():
    from embry0.services.github_sync import GitHubSyncService

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
    github = GitHubSyncService(client=FakeForge({"/repos/acme/web/issues": Listing(issues, complete=True)}))
    read = await github.list_backlog(["acme/web"])
    leads = await backlog_leads(
        read.items, trigger_labels=["embry0"], tracked=_untracked, min_score=40, readiness=DEFAULT
    )
    assert [lead.data["key"] for lead in leads] == ["1"] and read.whole == frozenset({"acme/web"})
    partial = GitHubSyncService(client=FakeForge({"/repos/acme/web/issues": Listing(issues, complete=False)}))
    assert (await partial.list_backlog(["acme/web"])).whole == frozenset()


async def test_a_forge_tracker_reads_with_the_client_it_is_given_for_each_repository():
    """The leads read a backlog with the organization's own credential: the client ``client_for`` gives, never the
    tracker's own; a repository it gives none for is not read."""
    from embry0.services.github_sync import GitHubSyncService

    issue = {"number": 1, "title": "Export fails on an empty start date", "body": READY_BODY, "labels": []}
    own = FakeForge({"/repos/acme/web/issues": Listing([{**issue, "number": 99}], complete=True)})
    given = FakeForge({"/repos/acme/web/issues": Listing([issue], complete=True)})

    async def client_for(repo: str) -> Any:
        return given if repo == "acme/web" else None

    read = await GitHubSyncService(client=own).list_backlog(["acme/web", "acme/api"], client_for=client_for)
    assert [item.key for item in read.items] == ["1"] and read.whole == frozenset({"acme/web"})
    assert own.asked == [], "the tracker's own client reads nothing"


async def test_gitlab_issues_nobody_holds_become_leads_read_from_its_own_projects():
    """The GitLab tracker reads each project's open issues over the real client (GitLab faked by its answers)."""
    import httpx

    from embry0.execution.gitlab_client import GitLabClient
    from embry0.services.gitlab_sync import GitLabSyncService

    issues = [
        {
            "iid": 7,
            "title": "Export fails on an empty start date",
            "description": READY_BODY,
            "labels": ["bug"],
            "web_url": "https://gitlab.example.test/acme/sub/web/-/issues/7",
        },
        {"iid": 8, "title": "Export fails again somewhere", "description": READY_BODY, "assignees": [{"id": 1}]},
        {"iid": 9, "title": "Export fails for embry0 already", "description": READY_BODY, "labels": ["embry0"]},
    ]
    asked: list[str] = []

    def forge(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.raw_path.decode())
        if request.url.raw_path.decode().split("?")[0].endswith("/projects/acme%2Fsub%2Fweb/issues"):
            return httpx.Response(200, json=issues)
        return httpx.Response(404, json={"message": "404 Not Found"})

    client = GitLabClient(
        default_token="key-000000000001",
        gitlab_url="https://gitlab.example.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(forge)),
    )
    read = await GitLabSyncService(client).list_backlog(["acme/sub/web", "acme/gone"])
    leads = await backlog_leads(
        read.items, trigger_labels=["embry0"], tracked=_untracked, min_score=40, readiness=DEFAULT
    )
    assert [(lead.data["tracker"], lead.data["key"]) for lead in leads] == [("gitlab", "7")]
    assert leads[0].url == "https://gitlab.example.test/acme/sub/web/-/issues/7"
    assert read.whole == frozenset({"acme/sub/web"}), "a project whose read failed is not read whole"
    assert "state=opened" in asked[0] and "order_by=updated_at" in asked[0]


async def test_a_repository_given_no_client_is_not_read_with_the_tracker_s_own():
    """The leads read a backlog with the organization's credential alone: a repository its organization gives no
    client for is not read, though the tracker's own client (the deployment's credential) would answer."""
    from embry0.services.github_sync import GitHubSyncService
    from embry0.services.gitlab_sync import GitLabSyncService
    from tests.unit.services.leads.fakes import fake_gitlab

    issue = {"number": 1, "iid": 1, "title": "Export fails on an empty start date", "body": READY_BODY, "labels": []}
    own_github = FakeForge({"/repos/acme/web/issues": Listing([issue], complete=True)})
    own_gitlab = fake_gitlab({"/projects/acme%2Fweb/issues": [{**issue, "description": READY_BODY}]})

    async def no_client(repo: str) -> Any:
        return None

    for tracker in (GitHubSyncService(client=own_github), GitLabSyncService(own_gitlab)):
        read = await tracker.list_backlog(["acme/web"], client_for=no_client)
        assert (read.items, read.whole) == ([], frozenset()), f"{tracker.name} fell back to its own client: {read}"
        assert read.unread == {"acme/web": "no client with the organization's credential"}
    assert own_github.asked == [], "the deployment's credential is never asked"


async def test_a_backlog_read_that_fails_or_stops_short_says_why():
    from embry0.services.github_sync import GitHubSyncService

    class Refusing(FakeForge):
        async def paginate_listing(self, path: str, **kwargs: Any) -> Listing:
            if "/broken/" in path:
                raise TimeoutError("read timed out")
            return await super().paginate_listing(path, **kwargs)

    issue = {"number": 1, "title": "Export fails on an empty start date", "body": READY_BODY, "labels": []}
    client = Refusing(
        {
            "/repos/acme/web/issues": Listing([issue], complete=True),
            "/repos/acme/big/issues": Listing([issue], complete=False),
        }
    )
    read = await GitHubSyncService(client=client).list_backlog(["acme/web", "acme/big", "acme/broken"])
    assert read.whole == frozenset({"acme/web"})
    assert read.unread == {
        "acme/big": "the read stopped before the end (its page cap, or a page refused)",
        "acme/broken": "the read failed (TimeoutError)",
    }
