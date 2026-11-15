"""Label parsing edge cases and inbound webhook event handling."""

import pytest

from embry0.storage.repositories.issues import VALID_ISSUE_TRANSITIONS

TRIGGERS = {"embry0"}


class FakeIssuesRepo:
    """In-memory stand-in that enforces the real status-transition table.

    Reusing ``VALID_ISSUE_TRANSITIONS`` is deliberate: a looser fake would hide
    the fact that ``in_progress -> triaging`` is not a legal edge, which is
    exactly the path a label on a running issue takes.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self._seq = 0

    def seed(self, *, repo: str, github_number: int, labels: list[str], status: str = "open") -> str:
        self._seq += 1
        issue_id = f"iss-{self._seq:012d}"
        self.rows[issue_id] = {
            "id": issue_id,
            "title": "seeded",
            "body": "",
            "labels": list(labels),
            "repo": repo,
            "tracker": "github",
            "tracker_key": str(github_number),
            "status": status,
            "created_by": "user",
        }
        return issue_id

    async def get_by_tracker_ref(self, tracker: str, repo: str | None, tracker_key: str) -> dict | None:
        for row in self.rows.values():
            if (
                row.get("tracker") == tracker
                and row.get("tracker_key") == tracker_key
                and (repo is None or row.get("repo") == repo)
            ):
                return dict(row)
        return None

    async def create(
        self,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
        repo: str | None = None,
        github_sync_enabled: bool = False,
        created_by: str = "user",
        **ref: object,
    ) -> str:
        self._seq += 1
        issue_id = f"iss-{self._seq:012d}"
        self.rows[issue_id] = {
            "id": issue_id,
            "title": title,
            "body": body,
            "labels": list(labels or []),
            "repo": repo,
            "tracker": ref.get("tracker"),
            "tracker_key": ref.get("tracker_key"),
            "status": "open",
            "created_by": created_by,
        }
        return issue_id

    async def update(self, issue_id: str, **fields: object) -> None:
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
            raise ValueError(f"Issue {issue_id} already has an active job")
        return f"job-{len(self.calls)}"


def _payload(number: int, labels: list[str], *, repo: str = "owner/repo", title: str = "Fix auth") -> dict:
    return {
        "repository": {"full_name": repo},
        "issue": {
            "number": number,
            "title": title,
            "body": "",
            "html_url": f"https://github.com/{repo}/issues/{number}",
            "labels": [{"name": name} for name in labels],
        },
    }


async def _handle(action: str, payload: dict, repo_obj: FakeIssuesRepo, executor: FakeExecutor | None) -> dict:
    from embry0.services.github_sync import GitHubSyncService
    from embry0.trackers import TriggerContext

    return await GitHubSyncService(github_token="t").handle_webhook_event(
        payload,
        TriggerContext(
            issues_repo=repo_obj,
            issue_executor=executor,
            trigger_labels=TRIGGERS,
            event_type="issues",
            action=action,
        ),
    )


def test_extract_labels_from_payload_skips_malformed_entries():
    """Malformed label entries must be ignored, not crash the delivery.

    Bare strings ARE accepted — the shared extractor serves GitLab too, and
    some GitLab payload shapes send them (#160). GitHub never does.
    """
    from embry0.trackers import extract_labels

    payload_labels = [
        {"name": "bug"},
        None,
        {"color": "red"},  # dict but no "name" key
        {"name": "embry0"},
    ]
    assert extract_labels(payload_labels) == ["bug", "embry0"]


def test_extract_labels_from_empty_list():
    from embry0.trackers import extract_labels

    assert extract_labels([]) == []


def test_extract_labels_from_none():
    """labels field missing/None should also be safe."""
    from embry0.trackers import extract_labels

    assert extract_labels(None) == []


def test_extract_labels_skips_non_string_name():
    """A dict with a non-string name value must be skipped."""
    from embry0.trackers import extract_labels

    assert extract_labels([{"name": 42}, {"name": "ok"}, {"name": None}]) == ["ok"]


@pytest.fixture(autouse=True)
def _clean_owner_tokens(monkeypatch):
    """Clean up GITHUB_TOKEN__ env vars for isolated tests."""
    import os

    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)


def test_headers_resolve_per_owner_token(monkeypatch):
    from embry0.services.github_sync import GitHubSyncService

    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "tok-rc")
    svc = GitHubSyncService(github_token="tok-default")
    assert svc._headers("acme-corp/widgets")["Authorization"] == "Bearer tok-rc"
    assert svc._headers("octo-org/embry0")["Authorization"] == "Bearer tok-default"
    assert svc._headers()["Authorization"] == "Bearer tok-default"  # back-compat


def test_headers_no_tokens_at_all(monkeypatch):
    from embry0.services.github_sync import GitHubSyncService

    monkeypatch.delenv("GITHUB_TOKEN__ACME_CORP", raising=False)
    svc = GitHubSyncService(github_token=None)
    assert "Authorization" not in svc._headers("acme-corp/widgets")


# --------------------------------------------------------------------------
# opened — pre-existing behaviour, previously untested
# --------------------------------------------------------------------------


async def test_opened_with_trigger_label_dispatches():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    result = await _handle("opened", _payload(1, ["embry0"]), repo, ex)
    assert result["status"] == "accepted"
    assert result["dispatched"] is True
    assert len(ex.calls) == 1
    assert repo.rows[result["issue_id"]]["status"] == "triaging"


async def test_opened_without_trigger_label_does_not_dispatch():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    result = await _handle("opened", _payload(1, ["bug"]), repo, ex)
    assert result["dispatched"] is False
    assert ex.calls == []
    assert repo.rows[result["issue_id"]]["status"] == "open"


# --------------------------------------------------------------------------
# labeled — the bug this change fixes
# --------------------------------------------------------------------------


async def test_labeling_tracked_issue_dispatches():
    """The regression: this used to sync the labels column and nothing else."""
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo="owner/repo", github_number=42, labels=["bug"])

    result = await _handle("labeled", _payload(42, ["bug", "embry0"]), repo, ex)

    assert result["dispatched"] is True
    assert ex.calls == [issue_id]
    assert repo.rows[issue_id]["labels"] == ["bug", "embry0"]
    assert repo.rows[issue_id]["status"] == "triaging"


async def test_labeling_untracked_issue_adopts_and_dispatches():
    repo, ex = FakeIssuesRepo(), FakeExecutor()

    result = await _handle("labeled", _payload(7, ["embry0"]), repo, ex)

    assert result["status"] == "accepted"
    assert result["action"] == "issue_adopted"
    assert result["dispatched"] is True
    row = repo.rows[result["issue_id"]]
    assert row["created_by"] == "webhook-label"
    assert row["tracker"] == "github"
    assert row["tracker_key"] == "7"
    assert ex.calls == [result["issue_id"]]


async def test_labeling_untracked_issue_without_trigger_stays_ignored():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    result = await _handle("labeled", _payload(99, ["bug"]), repo, ex)
    assert result == {"status": "ignored", "reason": "issue not tracked"}
    assert repo.rows == {}
    assert ex.calls == []


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------


async def test_unrelated_label_on_already_labelled_issue_does_not_redispatch():
    """Adding `bug` to an issue that already carries `embry0` must not re-fire."""
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    repo.seed(repo="owner/repo", github_number=42, labels=["embry0"])

    result = await _handle("labeled", _payload(42, ["embry0", "bug"]), repo, ex)

    assert result["dispatched"] is False
    assert ex.calls == []


async def test_redelivery_of_same_event_dispatches_once():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo="owner/repo", github_number=42, labels=[])

    first = await _handle("labeled", _payload(42, ["embry0"]), repo, ex)
    # Simulate the job finishing, so status alone would not block a re-fire.
    repo.rows[issue_id]["status"] = "open"
    second = await _handle("labeled", _payload(42, ["embry0"]), repo, ex)

    assert first["dispatched"] is True
    assert second["dispatched"] is False
    assert len(ex.calls) == 1


async def test_relabel_after_completion_dispatches_again():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo="owner/repo", github_number=42, labels=[])

    await _handle("labeled", _payload(42, ["embry0"]), repo, ex)
    repo.rows[issue_id]["status"] = "open"  # terminal paths return the issue to open
    await _handle("unlabeled", _payload(42, []), repo, ex)
    result = await _handle("labeled", _payload(42, ["embry0"]), repo, ex)

    assert result["dispatched"] is True
    assert ex.calls == [issue_id, issue_id]


# --------------------------------------------------------------------------
# status gating
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["triaging", "in_progress", "awaiting_input", "paused"])
async def test_label_on_issue_with_active_job_is_skipped(status):
    """in_progress -> triaging is not a legal edge; this must not raise."""
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo="owner/repo", github_number=42, labels=[], status=status)

    result = await _handle("labeled", _payload(42, ["embry0"]), repo, ex)

    assert result["status"] == "accepted"
    assert result["dispatched"] is False
    assert ex.calls == []
    assert repo.rows[issue_id]["status"] == status
    assert repo.rows[issue_id]["labels"] == ["embry0"]  # sync still happened


@pytest.mark.parametrize("status", ["closed", "cancelled"])
async def test_label_does_not_resurrect_finished_issue(status):
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo="owner/repo", github_number=42, labels=[], status=status)

    result = await _handle("labeled", _payload(42, ["embry0"]), repo, ex)

    assert result["dispatched"] is False
    assert ex.calls == []
    assert repo.rows[issue_id]["status"] == status


# --------------------------------------------------------------------------
# unlabeled stays sync-only; failure handling
# --------------------------------------------------------------------------


async def test_unlabeled_never_dispatches():
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo="owner/repo", github_number=42, labels=["embry0"])

    result = await _handle("unlabeled", _payload(42, []), repo, ex)

    assert result["action"] == "issue_unlabeled"
    assert result["dispatched"] is False
    assert ex.calls == []
    assert repo.rows[issue_id]["labels"] == []


async def test_dispatch_failure_reverts_status_to_open():
    """A stuck `triaging` row would swallow every future label trigger."""
    repo, ex = FakeIssuesRepo(), FakeExecutor(fail=True)
    issue_id = repo.seed(repo="owner/repo", github_number=42, labels=[])

    result = await _handle("labeled", _payload(42, ["embry0"]), repo, ex)

    assert result["dispatched"] is False
    assert repo.rows[issue_id]["status"] == "open"


async def test_no_executor_configured_is_safe():
    repo = FakeIssuesRepo()
    issue_id = repo.seed(repo="owner/repo", github_number=42, labels=[])

    result = await _handle("labeled", _payload(42, ["embry0"]), repo, None)

    assert result["dispatched"] is False
    assert repo.rows[issue_id]["status"] == "open"
    assert repo.rows[issue_id]["labels"] == ["embry0"]


# --------------------------------------------------------------------------
# case-insensitive matching (#157)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("applied", ["Embry0", "EMBRY0", "  embry0  "])
async def test_trigger_matching_is_case_insensitive(applied):
    """GitHub compared case-sensitively, so `Embry0` used to be silently inert."""
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    issue_id = repo.seed(repo="owner/repo", github_number=42, labels=[])

    result = await _handle("labeled", _payload(42, [applied]), repo, ex)

    assert result["dispatched"] is True
    assert ex.calls == [issue_id]


async def test_configured_trigger_label_casing_also_normalised():
    """The configured side is normalised too, not just the incoming label."""
    from embry0.services.github_sync import GitHubSyncService

    repo, ex = FakeIssuesRepo(), FakeExecutor()
    repo.seed(repo="owner/repo", github_number=42, labels=[])

    from embry0.trackers import TriggerContext

    result = await GitHubSyncService(github_token="t").handle_webhook_event(
        _payload(42, ["embry0"]),
        TriggerContext(
            issues_repo=repo,
            issue_executor=ex,
            trigger_labels={"EMBRY0"},
            event_type="issues",
            action="labeled",
        ),
    )

    assert result["dispatched"] is True


async def test_case_variant_relabel_is_still_idempotent():
    """`Embry0` must not read as "new" when `embry0` is already stored."""
    repo, ex = FakeIssuesRepo(), FakeExecutor()
    repo.seed(repo="owner/repo", github_number=42, labels=["embry0"])

    result = await _handle("labeled", _payload(42, ["Embry0", "bug"]), repo, ex)

    assert result["dispatched"] is False
    assert ex.calls == []


async def test_closing_an_issue_ends_its_jobs_answer_waits():
    """#518: a job that waits for answers on an issue closed on GitHub is cancelled, not left to be resumed."""

    class Ending(FakeExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.ended: list[str] = []

        async def end_answer_waits(self, issue_id: str) -> list[str]:
            self.ended.append(issue_id)
            return ["job-waiting"]

    repo, ex = FakeIssuesRepo(), Ending()
    issue_id = repo.seed(repo="owner/repo", github_number=42, labels=[], status="awaiting_input")

    result = await _handle("closed", _payload(42, []), repo, ex)

    assert repo.rows[issue_id]["status"] == "closed"
    assert ex.ended == [issue_id], f"closing the issue did not end its jobs' answer waits: {ex.ended}"
    assert result == {"status": "accepted", "action": "issue_closed", "cancelled_jobs": ["job-waiting"]}, result
