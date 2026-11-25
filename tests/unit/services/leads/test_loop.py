"""The leads loop (spec 2026-10-25): what it stores, caps and closes, and what it never closes.

Its policy is the ``leads`` section of the settings document, read through
``tests.fixtures.leads`` as the real snapshot reads it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx

from embry0.execution.forge_client import Listing
from embry0.services.areas import Area
from embry0.services.github_sync import GitHubSyncService
from embry0.services.gitlab_sync import GitLabSyncService
from embry0.services.leads.loop import FINDER_FORGES, LeadsLoop
from embry0.settings.sections.leads import LeadsSettings
from tests.fixtures.areas import areas_for
from tests.fixtures.leads import MemorySettings
from tests.unit.services.leads.fakes import FakeForge, MemoryLeads, fake_gitlab, linear_tracker
from tests.unit.services.leads.test_backlog import READY_BODY

REPO = "acme/web"
HEAD = "c" * 40
T0 = datetime(2026, 10, 25, 12, tzinfo=UTC)


#: A forge client that serves nothing: the trackers' own, which the leads never read with.
NO_FORGE: Any = SimpleNamespace()


def _ci(*failing: str) -> FakeForge:
    return FakeForge(
        {
            f"/repos/{REPO}": {"default_branch": "main"},
            f"/repos/{REPO}/commits/main": {"sha": HEAD},
            f"/repos/{REPO}/commits/{HEAD}/check-runs": {
                "total_count": len(failing),
                "check_runs": [{"name": n, "status": "completed", "conclusion": "failure"} for n in failing],
            },
            f"/repos/{REPO}/commits/{HEAD}/status": {"statuses": []},
        }
    )


def _loop(
    store: MemoryLeads,
    client: Any,
    *,
    finders: set[str],
    linear: Any = None,
    cap: int = 5,
    now=T0,
    forge: str | None = "github",
) -> LeadsLoop:
    """The loop over ``REPO`` on ``forge``, read with ``client`` (the organization's own forge's). Its trackers are
    GitHub's and GitLab's, whose own clients serve nothing, so a backlog they read was read with ``client``, and
    Linear (``linear``) when given."""
    clock = {"now": now}
    settings = MemorySettings({"enabled": True, "finders": sorted(finders), "max_new_per_repo_per_day": cap})

    async def targets(section: Any = None) -> list[str]:
        return [REPO]

    async def client_for(repo: str) -> Any:
        return client

    async def forge_of(repo: str) -> str | None:
        return forge

    async def trackers() -> dict[str, Any]:
        found: dict[str, Any] = {"github": GitHubSyncService(client=NO_FORGE), "gitlab": GitLabSyncService(NO_FORGE)}
        return found | ({} if linear is None else {"linear": linear})

    async def tracked(tracker: str, repo: str, key: str) -> bool:
        return False

    async def triggers() -> list[str]:
        return ["embry0"]

    loop = LeadsLoop(
        settings=settings.leads,
        leads=store,
        targets=targets,
        client_for=client_for,
        forge_of=forge_of,
        trackers=trackers,
        tracked=tracked,
        trigger_labels=triggers,
        # The repository's areas as embry0 keeps them: a failing check named build is in Web.
        areas_for=areas_for({REPO: [Area("Web", logs=("build",))]}),
        clock=lambda: clock["now"],
    )
    return loop


async def test_what_a_finder_sees_is_stored_with_its_area_and_closed_once_it_is_gone():
    store = MemoryLeads()
    summary = await _loop(store, _ci("build"), finders={"ci"}).tick()
    assert summary == {REPO: {"ci": {"found": 1, "new": 1}}}
    (row,) = store.open("ci")
    assert row["area"] == "Web" and row["source_key"] == "check:build"
    summary = await _loop(store, _ci(), finders={"ci"}, now=T0 + timedelta(hours=6)).tick()
    assert summary[REPO]["ci"] == {"gone": 1} and not store.open("ci")
    await _loop(store, _ci("build"), finders={"ci"}, now=T0 + timedelta(hours=12)).tick()
    (back,) = store.open("ci")
    assert back["first_seen"] == T0, "a lead seen again keeps when it was first seen"


async def test_each_finder_adds_at_most_its_daily_cap_of_new_leads():
    store = MemoryLeads()
    checks = [f"check-{i}" for i in range(7)]
    summary = await _loop(store, _ci(*checks), finders={"ci"}, cap=5).tick()
    assert summary[REPO]["ci"] == {"found": 7, "new": 5, "capped": 2}
    assert len(store.open("ci")) == 5
    again = await _loop(store, _ci(*checks), finders={"ci"}, cap=5, now=T0 + timedelta(hours=6)).tick()
    assert again[REPO]["ci"] == {"found": 7, "capped": 2}, "the day's cap is spent; the stored ones stay"
    tomorrow = await _loop(store, _ci(*checks), finders={"ci"}, cap=5, now=T0 + timedelta(days=1, hours=1)).tick()
    assert tomorrow[REPO]["ci"] == {"found": 7, "new": 2}


async def test_a_finder_that_cannot_read_closes_nothing_and_says_so_once():
    store = MemoryLeads()
    await _loop(store, _ci("build"), finders={"ci"}).tick()
    summary = await _loop(store, FakeForge({}), finders={"ci"}, now=T0 + timedelta(hours=6)).tick()
    assert summary[REPO]["ci"] == {"skipped": "the default branch could not be read"}
    assert len(store.open("ci")) == 1
    nothing = await _loop(store, None, finders={"ci", "alert"}).tick(backlog=False)
    assert nothing[REPO] == {"ci": {"skipped": "no forge client"}, "alert": {"skipped": "no forge client"}}


def _linear(*keys: str, fails: bool = False, repo: str = REPO) -> Any:
    """Linear's real ``list_backlog`` over a backlog of ``keys`` about ``repo``, or one whose read fails."""

    async def list_backlog_tickets() -> tuple[list[Any], bool]:
        if fails:
            raise RuntimeError("Linear is down")
        tickets = [
            SimpleNamespace(
                issue=SimpleNamespace(
                    key=k, title=f"Fix the export for {k}", body=READY_BODY, url=f"u/{k}", labels=["bug"]
                ),
                repo=repo,
                assigned=False,
                mine=False,
                priority=0,
                estimate=None,
            )
            for k in keys
        ]
        return tickets, True

    return linear_tracker(SimpleNamespace(list_backlog_tickets=list_backlog_tickets))


async def test_the_backlog_reads_both_trackers_and_keeps_leads_from_one_it_could_not_read():
    issues = [{"number": 9, "title": "Export fails on an empty start date", "body": READY_BODY, "labels": []}]
    client = FakeForge({f"/repos/{REPO}/issues": Listing(issues, complete=True)})
    store = MemoryLeads()
    summary = await _loop(store, client, finders={"ticket"}, linear=_linear("ENG-1", "ENG-2")).tick()
    assert summary[REPO]["ticket"] == {"found": 3, "new": 3}
    assert sorted(r["source_key"] for r in store.open("ticket")) == ["github:9", "linear:ENG-1", "linear:ENG-2"]
    later = T0 + timedelta(days=1, hours=1)
    await _loop(store, client, finders={"ticket"}, linear=_linear(fails=True), now=later).tick()
    assert len(store.open("ticket")) == 3, "Linear unread: its leads stay open"
    await _loop(store, client, finders={"ticket"}, linear=_linear("ENG-1"), now=later + timedelta(days=1)).tick()
    assert sorted(r["source_key"] for r in store.open("ticket")) == ["github:9", "linear:ENG-1"]


async def test_a_linear_ticket_is_the_repository_s_whatever_case_linear_names_it_in():
    store = MemoryLeads()
    summary = await _loop(store, None, finders={"ticket"}, linear=_linear("ENG-1", repo=REPO.upper())).tick()
    assert (summary[REPO]["ticket"]["found"], summary[REPO]["ticket"]["new"]) == (1, 1), summary


async def test_the_backlog_waits_for_its_own_interval():
    store = MemoryLeads()
    loop = _loop(store, FakeForge({}), finders={"ticket"}, linear=_linear("ENG-1"))
    assert "ticket" in (await loop.tick())[REPO]
    assert "ticket" not in (await loop.tick())[REPO], "the second pass of the day leaves the backlog alone"


# ---- every forge (#181) ----

GITLAB_REPO_PATH = "/projects/acme%2Fweb"


def _gitlab(*failing: str, issues: list[dict[str, Any]] | None = None, asked: list[str] | None = None) -> Any:
    """A real GitLab client over a faked GitLab: ``REPO``'s newest pipeline on main, with ``failing`` jobs, and its
    open issues."""
    return fake_gitlab(
        {
            GITLAB_REPO_PATH: {"default_branch": "main"},
            f"{GITLAB_REPO_PATH}/pipelines": [{"id": 31, "sha": HEAD}],
            f"{GITLAB_REPO_PATH}/pipelines/31/jobs": [
                {"name": name, "status": "failed", "web_url": f"https://gitlab.example.test/j/{name}"}
                for name in failing
            ]
            + [{"name": "lint", "status": "success"}, {"name": "audit", "status": "failed", "allow_failure": True}],
            f"{GITLAB_REPO_PATH}/issues": issues or [],
        },
        asked,
    )


async def test_a_gitlab_repository_s_ci_is_read_from_its_pipeline_jobs():
    store = MemoryLeads()
    summary = await _loop(store, _gitlab("build"), finders={"ci"}, forge="gitlab").tick()
    assert summary[REPO]["ci"] == {"found": 1, "new": 1}, "a job allowed to fail is no failure"
    (row,) = store.open("ci")
    assert row["source_key"] == "check:build" and row["area"] == "Web"


async def test_a_finder_on_a_forge_it_does_not_read_is_skipped_with_the_reason_on_the_status():
    store = MemoryLeads()
    loop = _loop(store, _gitlab(), finders={"ci", "alert", "review"}, forge="gitlab")
    summary = await loop.tick(backlog=False)
    assert summary[REPO]["alert"] == {"skipped": "alert reads github repositories; this one is on gitlab"}
    assert summary[REPO]["review"] == {"skipped": "review reads github repositories; this one is on gitlab"}
    assert summary[REPO]["ci"] == {}, "ci reads GitLab, and found nothing failing"
    status = loop.status()
    assert status["last_pass_at"] == T0.isoformat()
    assert status["repos"][REPO]["forge"] == "gitlab"
    assert status["repos"][REPO]["kinds"]["alert"]["skipped"].endswith("this one is on gitlab")


async def test_a_repository_on_a_forge_embry0_does_not_know_is_skipped_by_name():
    summary = await _loop(MemoryLeads(), FakeForge({}), finders={"ci", "flaky"}, forge=None).tick(backlog=False)
    assert summary[REPO] == {
        "ci": {"skipped": "ci reads github and gitlab repositories; this one is on a forge embry0 does not know"},
        "flaky": {"skipped": "flaky reads github and gitlab repositories; this one is on a forge embry0 does not know"},
    }


async def test_each_tracker_reads_the_backlog_of_the_repositories_it_serves():
    """A GitLab repository's backlog is read by GitLab's tracker and Linear, never by GitHub's."""
    issues = [{"iid": 5, "title": "Export fails on an empty start date", "description": READY_BODY, "labels": ["bug"]}]
    asked: list[str] = []
    store = MemoryLeads()
    loop = _loop(
        store, _gitlab(issues=issues, asked=asked), finders={"ticket"}, linear=_linear("ENG-1"), forge="gitlab"
    )
    summary = await loop.tick()
    assert summary[REPO]["ticket"] == {"found": 2, "new": 2}
    assert sorted(r["source_key"] for r in store.open("ticket")) == ["gitlab:5", "linear:ENG-1"]
    assert asked == [f"{GITLAB_REPO_PATH}/issues"], f"GitLab's tracker alone reads a GitLab repository: {asked}"


async def test_a_tracker_that_did_not_read_a_repository_whole_closes_none_of_its_leads():
    issues = [{"iid": 5, "title": "Export fails on an empty start date", "description": READY_BODY, "labels": ["bug"]}]
    store = MemoryLeads()
    await _loop(store, _gitlab(issues=issues), finders={"ticket"}, forge="gitlab").tick()
    assert [r["source_key"] for r in store.open("ticket")] == ["gitlab:5"]
    later = T0 + timedelta(days=1, hours=1)
    await _loop(store, fake_gitlab({}), finders={"ticket"}, forge="gitlab", now=later).tick()  # every read refused
    assert [r["source_key"] for r in store.open("ticket")] == ["gitlab:5"], "GitLab unread: its lead stays open"
    await _loop(store, None, finders={"ticket"}, forge="gitlab", now=later + timedelta(days=1)).tick()
    assert [r["source_key"] for r in store.open("ticket")] == ["gitlab:5"], "no client, nothing read: it stays open"
    await _loop(store, _gitlab(), finders={"ticket"}, forge="gitlab", now=later + timedelta(days=2)).tick()
    assert store.open("ticket") == [], "read whole and gone: closed"


def test_each_finder_reads_exactly_the_forges_it_declares():
    loop = _loop(MemoryLeads(), None, finders=set())
    finders = loop._finders(REPO, T0, LeadsSettings())
    assert {kind: frozenset(by) for kind, by in finders.items()} == {
        kind: forges for kind, forges in FINDER_FORGES.items() if kind != "ticket"
    }
    assert FINDER_FORGES["alert"] == FINDER_FORGES["review"] == frozenset({"github"})


# ---- a read that failed says why (#181 review) ----


async def test_a_finder_whose_read_fails_is_skipped_with_why_and_closes_nothing():
    store = MemoryLeads()
    await _loop(store, _gitlab("build"), finders={"ci"}, forge="gitlab").tick()
    assert [r["source_key"] for r in store.open("ci")] == ["check:build"]

    def times_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    later = T0 + timedelta(hours=6)
    broken = fake_gitlab({}, transport=times_out)
    loop = _loop(store, broken, finders={"ci"}, forge="gitlab", now=later)
    summary = await loop.tick()
    assert summary[REPO]["ci"] == {"skipped": "the read failed (ReadTimeout)"}
    assert loop.status()["repos"][REPO]["kinds"]["ci"] == {"skipped": "the read failed (ReadTimeout)"}
    assert [r["source_key"] for r in store.open("ci")] == ["check:build"], "nothing closes"


async def test_a_tracker_that_did_not_read_the_repository_whole_is_named_with_why():
    store = MemoryLeads()
    summary = await _loop(store, None, finders={"ticket"}, linear=_linear(fails=True), forge="gitlab").tick()
    assert summary[REPO]["ticket"] == {
        "unread": "linear: the read failed (RuntimeError); gitlab: no client with the organization's credential"
    }


async def test_linear_s_tickets_come_first_when_the_day_s_cap_cuts_tickets_of_one_severity():
    """As before the registry: a day's cap of one keeps Linear's ticket over GitHub's of the same severity."""
    issues = [{"number": 9, "title": "Export fails on an empty start date", "body": READY_BODY, "labels": []}]
    client = FakeForge({f"/repos/{REPO}/issues": Listing(issues, complete=True)})
    store = MemoryLeads()
    summary = await _loop(store, client, finders={"ticket"}, linear=_linear("ENG-1"), cap=1).tick()
    assert summary[REPO]["ticket"] == {"found": 2, "new": 1, "capped": 1}
    assert [r["source_key"] for r in store.open("ticket")] == ["linear:ENG-1"]


async def test_a_repository_that_left_the_leads_leaves_the_status():
    store = MemoryLeads()
    loop = _loop(store, _ci(), finders={"ci"})
    await loop.tick()
    assert set(loop.status()["repos"]) == {REPO}

    async def nobody(section: Any = None) -> list[str]:
        return []

    loop.targets = nobody
    await loop.tick()
    assert loop.status()["repos"] == {}, "a whole pass drops a repository it no longer reads"
