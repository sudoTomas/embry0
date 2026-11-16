"""GitLab issue trigger — label transitions, adoption, status gating (#165)."""

import pytest

from embry0.execution.gitlab_client import GitLabClient
from embry0.services.gitlab_sync import (
    TRACKER,
    GitLabSyncService,
    previous_label_titles,
)
from embry0.storage.repositories.issues import VALID_ISSUE_TRANSITIONS
from embry0.trackers import TriggerContext, extract_labels
from tests.fixtures.webhooks import gitlab_issue

TRIGGERS = {"embry0"}
PROJECT = "acme/products/fbr"


class FakeIssuesRepo:
    """In-memory stand-in enforcing the real status-transition table."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self._seq = 0

    def seed(self, *, repo: str, tracker_key: str, labels: list[str], status: str = "open") -> str:
        self._seq += 1
        issue_id = f"iss-{self._seq:012d}"
        self.rows[issue_id] = {
            "id": issue_id,
            "title": "seeded",
            "body": "",
            "labels": list(labels),
            "repo": repo,
            "tracker": TRACKER,
            "tracker_key": tracker_key,
            "status": status,
        }
        return issue_id

    async def get_by_tracker_ref(self, tracker: str, repo: str, tracker_key: str) -> dict | None:
        for row in self.rows.values():
            if (row.get("tracker"), row.get("repo"), row.get("tracker_key")) == (tracker, repo, tracker_key):
                return dict(row)
        return None

    async def create(self, title: str, body: str = "", labels=None, repo=None, created_by="user", **ref) -> str:
        self._seq += 1
        issue_id = f"iss-{self._seq:012d}"
        self.rows[issue_id] = {
            "id": issue_id,
            "title": title,
            "body": body,
            "labels": list(labels or []),
            "repo": repo,
            "status": "open",
            "created_by": created_by,
            "tracker": ref.get("tracker"),
            "tracker_key": ref.get("tracker_key"),
        }
        return issue_id

    async def update(self, issue_id: str, **fields) -> None:
        row = self.rows[issue_id]
        if "status" in fields:
            current, new = row["status"], fields["status"]
            if current != new and new not in VALID_ISSUE_TRANSITIONS.get(current, set()):
                raise ValueError(f"Invalid issue status transition: {current} -> {new}")
        row.update(fields)


class FakeExecutor:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.dispatched_by: list[str | None] = []
        self.fail = fail

    async def execute(self, issue_id: str, dispatched_by: str | None = None) -> str:
        self.calls.append(issue_id)
        self.dispatched_by.append(dispatched_by)
        if self.fail:
            raise ValueError("active job")
        return f"job-{len(self.calls)}"


def _svc(repo_map=None) -> GitLabSyncService:
    return GitLabSyncService(GitLabClient(default_token="glpat-x"), repo_map or {})


async def _handle(payload, repo, ex, *, svc=None):
    return await (svc or _svc()).handle_webhook_event(
        payload, TriggerContext(issues_repo=repo, issue_executor=ex, trigger_labels=TRIGGERS)
    )


# --- payload shape ----------------------------------------------------------


def test_labels_are_keyed_title_not_name():
    """GitHub uses `name`; GitLab uses `title`. Reading the wrong one is silent."""
    assert extract_labels([{"title": "embry0"}, {"title": "bug"}]) == ["embry0", "bug"]
    assert extract_labels([{"name": "embry0"}]) == ["embry0"]  # tolerated
    assert extract_labels(["bare-string"]) == ["bare-string"]
    assert extract_labels([{"id": 1}, None, 42]) == []
    assert extract_labels(None) == []


def test_previous_labels_only_present_when_labels_changed():
    """No `changes.labels` means labels did not change in this delivery."""
    assert previous_label_titles(gitlab_issue(action="update", labels=["embry0"])) is None
    payload = gitlab_issue(action="update", labels=["embry0"], previous_labels=["bug"])
    assert previous_label_titles(payload) == ["bug"]


# --- the trigger ------------------------------------------------------------


async def test_label_transition_dispatches():
    """`embry0` newly applied → dispatch. There is no `labeled` action to key on."""
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo=PROJECT, tracker_key="11", labels=["bug"])

    result = await _handle(
        gitlab_issue(action="update", iid=11, labels=["bug", "embry0"], previous_labels=["bug"]),
        repo,
        ex,
    )

    assert result["dispatched"] is True
    assert ex.calls == [issue_id]
    assert repo.rows[issue_id]["status"] == "triaging"


async def test_untracked_issue_is_adopted_and_dispatched():
    repo, ex = FakeIssuesRepo(), FakeExecutor()

    result = await _handle(gitlab_issue(action="open", iid=7, labels=["embry0"]), repo, ex)

    assert result["dispatched"] is True
    row = repo.rows[result["issue_id"]]
    assert row["tracker"] == TRACKER
    assert row["tracker_key"] == "7"
    assert row["created_by"] == "gitlab-webhook"
    assert row["repo"] == PROJECT  # three segments, identity-mapped


async def test_unrelated_edit_on_an_already_labelled_issue_does_not_redispatch():
    """The trap: every `update` carries the full label set, trigger included."""
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    repo.seed(repo=PROJECT, tracker_key="11", labels=["embry0"])

    # Title edited; labels unchanged, so GitLab sends no changes.labels.
    result = await _handle(gitlab_issue(action="update", iid=11, labels=["embry0"]), repo, ex)

    assert result["dispatched"] is False
    assert ex.calls == []


async def test_adding_an_unrelated_label_does_not_redispatch():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    repo.seed(repo=PROJECT, tracker_key="11", labels=["embry0"])

    result = await _handle(
        gitlab_issue(action="update", iid=11, labels=["embry0", "bug"], previous_labels=["embry0"]),
        repo,
        ex,
    )

    assert result["dispatched"] is False
    assert ex.calls == []


async def test_no_trigger_label_is_a_no_op():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    result = await _handle(gitlab_issue(action="open", iid=3, labels=["bug"]), repo, ex)
    assert result["dispatched"] is False
    assert repo.rows == {}


# --- status gating ----------------------------------------------------------


@pytest.mark.parametrize("status", ["triaging", "in_progress", "awaiting_input", "paused"])
async def test_issue_with_an_active_job_is_not_redispatched(status):
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo=PROJECT, tracker_key="11", labels=[], status=status)

    result = await _handle(gitlab_issue(action="update", iid=11, labels=["embry0"], previous_labels=[]), repo, ex)

    assert result["dispatched"] is False
    assert ex.calls == []
    assert repo.rows[issue_id]["status"] == status
    assert repo.rows[issue_id]["labels"] == ["embry0"]  # sync still happened


@pytest.mark.parametrize("status", ["closed", "cancelled"])
async def test_label_does_not_resurrect_a_finished_issue(status):
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    repo.seed(repo=PROJECT, tracker_key="11", labels=[], status=status)
    result = await _handle(gitlab_issue(action="update", iid=11, labels=["embry0"], previous_labels=[]), repo, ex)
    assert result["dispatched"] is False


async def test_dispatch_failure_reverts_to_open():
    repo, ex = FakeIssuesRepo(), FakeExecutor(fail=True)
    issue_id = repo.seed(repo=PROJECT, tracker_key="11", labels=[])
    result = await _handle(gitlab_issue(action="update", iid=11, labels=["embry0"], previous_labels=[]), repo, ex)
    assert result["dispatched"] is False
    assert repo.rows[issue_id]["status"] == "open"


# --- other actions ----------------------------------------------------------


async def test_close_marks_the_issue_closed():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo=PROJECT, tracker_key="11", labels=["embry0"])
    result = await _handle(gitlab_issue(action="close", iid=11, labels=["embry0"]), repo, ex)
    assert result["action"] == "issue_closed"
    assert repo.rows[issue_id]["status"] == "closed"
    assert ex.calls == []


async def test_non_issue_events_are_ignored():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    assert (await _handle({"object_kind": "push"}, repo, ex))["status"] == "ignored"
    assert (await _handle({"object_kind": "issue"}, repo, ex))["status"] == "ignored"


async def test_missing_project_path_is_ignored():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    payload = gitlab_issue(action="open", labels=["embry0"])
    payload["project"] = {}
    assert (await _handle(payload, repo, ex))["status"] == "ignored"


# --- repo mapping -----------------------------------------------------------


async def test_repo_map_overrides_the_project_path():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    svc = _svc({PROJECT: "mirror/fbr"})
    result = await _handle(gitlab_issue(action="open", iid=9, labels=["embry0"]), repo, ex, svc=svc)
    assert repo.rows[result["issue_id"]]["repo"] == "mirror/fbr"


async def test_trigger_matching_is_case_insensitive():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    result = await _handle(gitlab_issue(action="open", iid=4, labels=["Embry0"]), repo, ex)
    assert result["dispatched"] is True


async def test_closing_an_issue_ends_its_jobs_answer_waits():
    """#518: as on GitHub, a closed issue's jobs waiting for answers are cancelled."""

    class Ending(FakeExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.ended: list[str] = []

        async def end_waits(self, issue_id: str) -> list[str]:
            self.ended.append(issue_id)
            return []

    repo, ex = FakeIssuesRepo(), Ending()
    issue_id = repo.seed(repo=PROJECT, tracker_key="11", labels=["embry0"], status="awaiting_input")
    result = await _handle(gitlab_issue(action="close", iid=11, labels=["embry0"]), repo, ex)
    assert repo.rows[issue_id]["status"] == "closed"
    assert ex.ended == [issue_id], f"closing the issue did not end its jobs' answer waits: {ex.ended}"
    assert result == {"status": "accepted", "action": "issue_closed"}, result


@pytest.mark.parametrize("status", ["closed", "cancelled"])
async def test_a_reopened_issue_reads_open_again(status):
    """#521: as a reopened GitHub issue does; a close is what made it read as ended and refused its jobs."""
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo=PROJECT, tracker_key="11", labels=["embry0"], status=status)
    result = await _handle(gitlab_issue(action="reopen", iid=11, labels=["embry0"]), repo, ex)
    assert repo.rows[issue_id]["status"] == "open", f"a reopened {status} issue reads {repo.rows[issue_id]['status']}"
    assert ex.calls == [], "a reopen starts a run by itself"
    assert result["action"] == "issue_reopen", result


async def test_a_reopen_of_an_issue_that_never_closed_writes_nothing():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo=PROJECT, tracker_key="11", labels=["embry0"], status="in_progress")
    await _handle(gitlab_issue(action="reopen", iid=11, labels=["embry0"]), repo, ex)
    assert repo.rows[issue_id]["status"] == "in_progress"
