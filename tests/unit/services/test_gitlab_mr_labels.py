"""A merge request embry0 opens on GitLab carries its labels, as a pull request does (#477).

``_open_change_request`` built the labels for both forges (``embry0``, and
``human-merge-only`` for a failed or salvaged job), but only the GitHub branch
passed them on: ``ensure_mr_for_branch`` took none. Now the merge request it
opens or finds gets them, and when GitLab will not take them the merge request
says so in one note, and the opener's diagnostics say so for the job (#472).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

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
    ):
        self.existing, self.labels_status, self.notes, self.conflict = existing, labels_status, notes or [], conflict
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
            message = {"message": "403 Forbidden"} if self.labels_status != 200 else {"iid": 7}
            return httpx.Response(self.labels_status, json=message)
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


async def test_a_merge_request_it_opens_gets_its_labels() -> None:
    forge, diagnostics = Forge(), {}
    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=forge.client(), labels=LABELS, diagnostics=diagnostics
    )
    assert url == URL
    assert forge.labelled() == [{"add_labels": "embry0,human-merge-only"}], forge.calls
    assert diagnostics == {} and forge.notes_sent() == [], "labels that went on are not reported"


async def test_a_merge_request_it_finds_gets_its_labels() -> None:
    forge = Forge(existing=True)
    url = await ensure_mr_for_branch(
        repo=REPO, branch=BRANCH, title="t", body="b", client=forge.client(), labels=LABELS
    )
    assert url == URL
    assert not [c for c in forge.calls if c[0] == "POST" and c[1].endswith("/merge_requests")], "no duplicate"
    assert forge.labelled() == [{"add_labels": "embry0,human-merge-only"}], forge.calls


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

    again = Forge(existing=True, labels_status=403, notes=[{"id": 30, "body": f"{LABELS_NOTE_MARKER}\n\nold"}])
    await ensure_mr_for_branch(repo=REPO, branch=BRANCH, title="t", body="b", client=again.client(), labels=LABELS)
    assert [method for method, _ in again.notes_sent()] == ["PUT"], (
        f"the note is edited, not posted again: {again.calls}"
    )


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
