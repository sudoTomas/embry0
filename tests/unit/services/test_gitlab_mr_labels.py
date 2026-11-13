"""A merge request embry0 opens on GitLab carries its labels, as a pull request does (#477).

``_open_change_request`` built the labels for both forges (``embry0``, and
``human-merge-only`` for a failed or salvaged job), but only the GitHub branch
passed them on: ``ensure_mr_for_branch`` took none. Now the merge request it
opens or finds gets them, and when GitLab will not take them the merge request
says so in one note, and the opener's diagnostics say so for the job (#472).
GitLab can answer 200 and leave labels off (a role that may not set them), so
the merge request it answers with is read back; and when a later outcome does
put them on, the note is edited to say so.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from embry0.execution.gitlab_client import GitLabClient
from embry0.services.github_prs import LABELS_NOTE_MARKER
from embry0.services.gitlab_mrs import ensure_mr_for_branch

REPO = "acme/products/fbt"
BRANCH = "embry0/iss-abcd1234-fix"
LABELS = ["embry0", "human-merge-only"]
URL = "https://gitlab.example.com/acme/products/fbt/-/merge_requests/7"


class Forge:
    """GitLab's merge requests of one project: ``existing`` open on the branch or none, labels taken or refused."""

    def __init__(
        self,
        *,
        existing: bool = False,
        labels_status: int = 200,
        notes: list[dict[str, Any]] | None = None,
        conflict: bool = False,
        carried: list[Any] | None = None,
    ):
        self.existing, self.labels_status, self.notes, self.conflict = existing, labels_status, notes or [], conflict
        #: The labels a 200 answers with; None: the ones asked for, as GitLab does when the role may set them.
        self.carried = carried
        self.calls: list[tuple[str, str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, body = request.url.path, json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if path.endswith("/merge_requests") and request.method == "GET":
            return httpx.Response(200, json=[{"iid": 7, "web_url": URL}] if self.existing else [])
        if path.endswith("/merge_requests") and request.method == "POST":
            if self.conflict:  # opened by someone else between the lookup and the create
                self.existing = True
                return httpx.Response(409, json={"message": ["Another open merge request already exists"]})
            return httpx.Response(201, json={"iid": 7, "web_url": URL})
        if path.endswith("/merge_requests/7") and request.method == "PUT":
            if self.labels_status != 200:
                return httpx.Response(self.labels_status, json={"message": "403 Forbidden"})
            carried = body["add_labels"].split(",") if self.carried is None else self.carried
            return httpx.Response(200, json={"iid": 7, "web_url": URL, "labels": carried})
        if path.endswith("/merge_requests/7/notes") and request.method == "GET":
            return httpx.Response(200, json=self.notes)
        if path.endswith("/merge_requests/7/notes") and request.method == "POST":
            return httpx.Response(201, json={"id": 31, **body})
        if "/merge_requests/7/notes/" in path and request.method == "PUT":
            return httpx.Response(200, json={"id": int(path.rsplit("/", 1)[1]), **body})
        if path.endswith("/projects/acme%2Fproducts%2Ffbt") or path.endswith("/projects/acme/products/fbt"):
            return httpx.Response(200, json={"id": 1, "default_branch": "main"})
        return httpx.Response(404, json={})

    def client(self) -> GitLabClient:
        return GitLabClient(
            default_token="glpat-x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        )

    def labelled(self) -> list[Any]:
        return [body for method, path, body in self.calls if method == "PUT" and path.endswith("/merge_requests/7")]

    def notes_sent(self) -> list[tuple[str, Any]]:
        return [(method, body) for method, path, body in self.calls if "/notes" in path and method in ("POST", "PUT")]

    def notes_read(self) -> int:
        return len([path for method, path, _ in self.calls if method == "GET" and path.endswith("/notes")])


async def test_a_merge_request_it_opens_gets_its_labels() -> None:
    forge, diagnostics = Forge(), {}
    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=forge.client(), labels=LABELS, diagnostics=diagnostics
    )
    assert url == URL
    assert forge.labelled() == [{"add_labels": "embry0,human-merge-only"}], forge.calls
    assert diagnostics == {} and forge.notes_sent() == [], "labels that went on are not reported"
    assert forge.notes_read() == 0, f"a merge request it has just opened has no note to look for: {forge.calls}"


async def test_a_merge_request_it_finds_gets_its_labels() -> None:
    forge = Forge(existing=True)
    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=forge.client(), labels=LABELS
    )
    assert url == URL
    assert not [c for c in forge.calls if c[0] == "POST" and c[1].endswith("/merge_requests")], "no duplicate"
    assert forge.labelled() == [{"add_labels": "embry0,human-merge-only"}], forge.calls
    assert forge.notes_sent() == [], f"no note where there was none to correct: {forge.calls}"


async def test_a_merge_request_found_after_a_conflict_gets_its_labels() -> None:
    forge = Forge(conflict=True)
    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=forge.client(), labels=LABELS
    )
    assert url == URL
    assert forge.labelled() == [{"add_labels": "embry0,human-merge-only"}], forge.calls


async def test_labels_gitlab_refuses_are_said_once_on_the_merge_request_and_to_the_job() -> None:
    forge, diagnostics = Forge(labels_status=403), {}
    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=forge.client(), labels=LABELS, diagnostics=diagnostics
    )
    assert url == URL, "a merge request without its labels is still the job's"
    sent = forge.notes_sent()
    assert len(sent) == 1 and sent[0][0] == "POST", sent
    note = sent[0][1]["body"]
    assert note.startswith(LABELS_NOTE_MARKER) and "`human-merge-only`" in note and "only a person" in note, note
    assert diagnostics["labels_missing"] == LABELS and diagnostics["labels_noted"] is True, diagnostics
    assert "403" in diagnostics["labels_error"], diagnostics
    assert "(403 Forbidden)" in note and "403 403" not in note, f"the status is said twice: {note}"

    again = Forge(existing=True, labels_status=403, notes=[{"id": 30, "body": f"{LABELS_NOTE_MARKER}\n\nold"}])
    await ensure_mr_for_branch(repo=REPO, branch=BRANCH, title="t", body="b", client=again.client(), labels=LABELS)
    assert [method for method, _ in again.notes_sent()] == ["PUT"], (
        f"the note is edited, not posted again: {again.calls}"
    )


@pytest.mark.parametrize(
    ("carried", "missing"),
    [
        ([], LABELS),
        (["embry0"], ["human-merge-only"]),
        ([{"id": 3, "name": "embry0"}], ["human-merge-only"]),
    ],
    ids=["none", "some", "detailed"],
)
async def test_labels_gitlab_answers_200_without_are_missing_too(carried: list[Any], missing: list[str]) -> None:
    """A role that may not set labels gets 200 and the merge request without them: that is a refusal too."""
    forge, diagnostics = Forge(carried=carried), {}
    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=forge.client(), labels=LABELS, diagnostics=diagnostics
    )
    assert url == URL
    assert diagnostics.get("labels_missing") == missing, f"labels GitLab left off are not reported: {diagnostics}"
    assert "200" in diagnostics["labels_error"] and diagnostics["labels_noted"] is True, diagnostics
    sent = forge.notes_sent()
    assert len(sent) == 1 and sent[0][0] == "POST", sent
    note = sent[0][1]["body"]
    assert "`human-merge-only`" in note and "only a person" in note, note
    assert ("`embry0`" in note) == ("embry0" in missing), f"the note names only the labels that are missing: {note}"


async def test_an_answer_that_names_no_labels_is_taken_as_their_going_on() -> None:
    """A 200 whose body says nothing of labels is not read as a refusal, nor said to have put them on."""
    notes = [{"id": 30, "body": f"{LABELS_NOTE_MARKER}\n\nembry0 could not add the labels"}]
    forge, diagnostics = Forge(existing=True, notes=notes), {}

    def bare(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path.endswith("/merge_requests/7"):
            forge.calls.append((request.method, request.url.path, json.loads(request.content)))
            return httpx.Response(200, text="ok")
        return forge.handler(request)

    client = GitLabClient(default_token="glpat-x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(bare)))
    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=client, labels=LABELS, diagnostics=diagnostics
    )
    assert url == URL and diagnostics == {}, diagnostics
    assert forge.notes_sent() == [], f"an answer that names no labels does not say they are on: {forge.calls}"


async def test_a_later_outcome_that_puts_the_labels_on_edits_the_note() -> None:
    """The note that said the labels were missing does not stay saying so once they are on."""
    notes = [{"id": 30, "body": f"{LABELS_NOTE_MARKER}\n\nembry0 could not add the labels"}]
    forge, diagnostics = Forge(existing=True, notes=notes), {}
    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=forge.client(), labels=LABELS, diagnostics=diagnostics
    )
    assert url == URL and diagnostics == {}, diagnostics
    edits = [(m, p, b) for m, p, b in forge.calls if "/notes/" in p and m == "PUT"]
    assert len(edits) == 1 and edits[0][1].endswith("/notes/30"), f"the old note is not corrected: {forge.calls}"
    body = edits[0][2]["body"]
    assert body.startswith(LABELS_NOTE_MARKER) and "could not" not in body, body
    assert "`embry0`, `human-merge-only` are on this merge request now" in body, body
    assert not [c for c in forge.calls if c[0] == "POST" and "/notes" in c[1]], "no new note"


async def test_a_note_that_cannot_be_corrected_leaves_the_merge_request_as_it_is() -> None:
    forge = Forge(existing=True)

    def no_notes(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/notes"):
            forge.calls.append((request.method, request.url.path, None))
            return httpx.Response(500, json={"message": "500 Internal Server Error"})
        return forge.handler(request)

    client = GitLabClient(
        default_token="glpat-x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(no_notes))
    )
    diagnostics: dict[str, Any] = {}
    with patch("asyncio.sleep", new=AsyncMock()):
        url = await ensure_mr_for_branch(
            repo=REPO, branch=BRANCH, title="t", body="b", client=client, labels=LABELS, diagnostics=diagnostics
        )
    assert url == URL and diagnostics == {}, diagnostics


@pytest.mark.parametrize(
    ("forge", "said"),
    [(lambda: Forge(labels_status=403), "403 Forbidden"), (lambda: Forge(carried=[]), "200")],
    ids=["refused", "left-off"],
)
async def test_a_failed_job_records_its_merge_requests_missing_labels(forge: Any, said: str) -> None:
    """End to end on GitLab: the job's events say its merge request has no ``human-merge-only`` (#477, #472)."""
    from embry0.safety.error_codes import ErrorCode
    from tests.unit.services.test_failed_job_pr import _executor, _failed, _terminal

    ex = _executor()
    ex._config = SimpleNamespace(
        environment_secret_key="k",
        pr_dedup_enabled=False,
        gitlab_token="glpat-x",
        gitlab_url="https://gitlab.example.com",
        gitlab_ca_bundle="",
    )
    ex._credential_resolver = None
    ex._repos = MagicMock(forge_for=AsyncMock(return_value="gitlab"))
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    forge = forge()
    with (
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch("embry0.orchestration.checkpoint.purge_thread", new=AsyncMock()),
        patch("embry0.services._executor_outcomes._base_branch_for_change_request", new=AsyncMock(return_value="main")),
        patch("embry0.execution.gitlab_client.make_gitlab_client", return_value=forge.client()),
    ):
        await ex._handle_workflow_result("iss-1", "job-1", _failed(ErrorCode.QA_FAILURES_UNRESOLVED.value))

    assert forge.labelled() == [{"add_labels": "embry0,human-merge-only"}], forge.calls
    messages = [
        str(call.args[1].get("message"))
        for call in ex._broadcast_event.await_args_list
        if call.args[1].get("type") == "system" and "labels" in str(call.args[1].get("message"))
    ]
    assert len(messages) == 1, f"the job does not record that its merge request has no labels: {messages}"
    assert URL in messages[0] and "human-merge-only" in messages[0] and said in messages[0], messages[0]
    assert messages[0].endswith("A comment on it says so."), messages[0]
    assert _terminal(ex)["pr_url"] == URL


async def test_a_failed_jobs_merge_request_on_gitlab_carries_human_merge_only() -> None:
    """``_open_change_request`` on a GitLab repository passes the job's labels to the merge request."""
    from embry0.services._executor_outcomes import _open_change_request
    from embry0.services.issue_executor import IssueExecutor

    ex = IssueExecutor.__new__(IssueExecutor)
    ex._config = SimpleNamespace(gitlab_token="glpat-x", gitlab_url="https://gitlab.example.com", gitlab_ca_bundle="")
    ex._credential_resolver = None
    ex._repos = MagicMock(forge_for=AsyncMock(return_value="gitlab"))
    forge = Forge()
    with patch("embry0.execution.gitlab_client.make_gitlab_client", return_value=forge.client()):
        url = await _open_change_request(
            ex, repo=REPO, branch=BRANCH, title="t", body="b", labels=LABELS, target_branch="main"
        )
    assert url == URL
    assert forge.labelled() == [{"add_labels": "embry0,human-merge-only"}], (
        f"no labels on the merge request: {forge.calls}"
    )
