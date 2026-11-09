"""The sticky note on a change request a job delivers unfinished work onto, and its settling (#182 review).

The real GitHub and GitLab clients over a mock transport, so what reaches
each forge is what is checked: one comment found again by its marker,
created or updated, the ``human-merge-only`` label when asked, and, once a
later job finished the work, that comment edited and nothing posted where
there is none.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from embry0.execution.github_client import GitHubClient
from embry0.execution.gitlab_client import GitLabClient

GITHUB_PR = "https://github.com/acme/widgets/pull/9"
GITLAB_MR = "https://gitlab.example.com/acme/sub/widgets/-/merge_requests/4"


def _module() -> Any:
    try:
        from embry0.services import unfinished_note
    except ImportError:
        pytest.fail("embry0.services.unfinished_note is missing: no note is kept on a change request a job had")
    return unfinished_note


class _Forge:
    """A mock forge: the comments (notes) it holds, and every call it answered."""

    def __init__(self, comments: list[dict[str, Any]], *, read: int = 200, write: int = 0) -> None:
        self.comments = comments
        self.read = read
        self.write = write
        self.calls: list[tuple[str, str, Any]] = []
        self.raw: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        self.raw.append(request.url.raw_path.decode().split("?")[0])
        if request.method == "GET":
            return httpx.Response(self.read, json=self.comments if self.read == 200 else {"message": "refused"})
        if self.write:
            return httpx.Response(self.write, json={"message": "refused"})
        if request.method == "POST" and request.url.path.endswith(("/comments", "/notes")):
            return httpx.Response(201, json={"id": 99})
        return httpx.Response(200, json={"id": 1})

    def writes(self) -> list[tuple[str, str, Any]]:
        return [call for call in self.calls if call[0] != "GET"]


def _patched(forge: _Forge) -> Any:
    module = _module()

    def github(**_kwargs: Any) -> GitHubClient:
        return GitHubClient(default_token="tok", http_client=httpx.AsyncClient(transport=httpx.MockTransport(forge)))

    async def _no_sleep(_seconds: float) -> None:
        return None

    def gitlab(*_args: Any, **_kwargs: Any) -> GitLabClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(forge))
        return GitLabClient(default_token="glpat-abc", http_client=http, sleep=_no_sleep)

    return (
        patch.object(module, "GitHubClient", side_effect=github),
        patch.object(module, "make_gitlab_client", side_effect=gitlab),
    )


async def _post(pr_url: str, forge: _Forge, *, human_merge_only: bool) -> bool:
    module = _module()
    a, b = _patched(forge)
    with a, b:
        return bool(
            await module.post_unfinished_note(
                pr_url,
                ["**embry0 did not finish this job:** why.", "Done-when items not met:\n- A"],
                human_merge_only=human_merge_only,
                access=module.ForgeAccess(),
            )
        )


async def _settle(pr_url: str, forge: _Forge) -> bool:
    module = _module()
    a, b = _patched(forge)
    with a, b:
        return bool(await module.settle_unfinished_note(pr_url, "job-2", access=module.ForgeAccess()))


@pytest.mark.asyncio
async def test_on_github_the_note_is_one_comment_and_the_label_when_only_a_person_may_merge() -> None:
    forge = _Forge([{"id": 5, "body": "unrelated"}])
    assert await _post(GITHUB_PR, forge, human_merge_only=True)
    writes = forge.writes()
    assert [(m, p) for m, p, _ in writes] == [
        ("POST", "/repos/acme/widgets/issues/9/comments"),
        ("POST", "/repos/acme/widgets/issues/9/labels"),
    ], writes
    body = writes[0][2]["body"]
    assert body.startswith(_module().MARKER) and "Done-when items not met:\n- A" in body, body
    assert writes[1][2] == {"labels": ["human-merge-only"]}


@pytest.mark.asyncio
async def test_on_github_a_second_note_updates_the_first_and_no_label_unless_asked() -> None:
    marker = _module().MARKER
    forge = _Forge([{"id": 5, "body": f"{marker}\n\nolder"}])
    assert await _post(GITHUB_PR, forge, human_merge_only=False)
    assert [(m, p) for m, p, _ in forge.writes()] == [("PATCH", "/repos/acme/widgets/issues/comments/5")], forge.calls


@pytest.mark.asyncio
async def test_on_github_a_later_job_s_finish_edits_the_note_and_posts_none_where_there_is_none() -> None:
    marker = _module().MARKER
    forge = _Forge([{"id": 5, "body": "unrelated"}, {"id": 6, "body": f"{marker}\n\nolder"}])
    assert await _settle(GITHUB_PR, forge) is True
    ((method, path, body),) = forge.writes()
    assert (method, path) == ("PATCH", "/repos/acme/widgets/issues/comments/6")
    assert body["body"].startswith(marker) and "Job `job-2` completed" in body["body"], body

    bare = _Forge([{"id": 5, "body": "unrelated"}])
    assert await _settle(GITHUB_PR, bare) is False
    assert bare.writes() == [], f"a pull request with no note gets nothing: {bare.writes()}"


@pytest.mark.asyncio
async def test_on_gitlab_the_note_is_one_note_and_the_label() -> None:
    forge = _Forge([])
    assert await _post(GITLAB_MR, forge, human_merge_only=True)
    writes = forge.writes()
    assert [m for m, _, _ in writes] == ["POST", "PUT"], writes
    assert writes[0][1].endswith("/merge_requests/4/notes") and writes[0][2]["body"].startswith(_module().MARKER)
    assert writes[1][2] == {"add_labels": "human-merge-only"}


@pytest.mark.asyncio
async def test_on_gitlab_a_later_job_s_finish_edits_the_note_and_posts_none_where_there_is_none() -> None:
    marker = _module().MARKER
    forge = _Forge([{"id": 3, "body": f"{marker}\n\nolder"}])
    assert await _settle(GITLAB_MR, forge) is True
    ((method, path, body),) = forge.writes()
    assert method == "PUT" and path.endswith("/merge_requests/4/notes/3") and "Job `job-2` completed" in body["body"]

    bare = _Forge([{"id": 3, "body": "unrelated"}])
    assert await _settle(GITLAB_MR, bare) is False
    assert bare.writes() == [], f"a merge request with no note gets nothing: {bare.writes()}"


@pytest.mark.asyncio
async def test_a_url_that_names_no_change_request_reaches_no_forge() -> None:
    forge = _Forge([])
    assert await _post("https://example.com/not/a/pr", forge, human_merge_only=True) is False
    assert await _settle("", forge) is False
    assert forge.calls == []


@pytest.mark.asyncio
async def test_on_gitlab_a_second_note_updates_the_first() -> None:
    marker = _module().MARKER
    forge = _Forge([{"id": 3, "body": f"{marker}\n\nolder"}])
    assert await _post(GITLAB_MR, forge, human_merge_only=False)
    ((method, path, _body),) = forge.writes()
    assert method == "PUT" and path.endswith("/merge_requests/4/notes/3"), forge.calls


@pytest.mark.asyncio
async def test_on_gitlab_a_subgroup_project_is_one_encoded_path_segment() -> None:
    forge = _Forge([])
    assert await _post(GITLAB_MR, forge, human_merge_only=False)
    assert forge.raw[0] == "/api/v4/projects/acme%2Fsub%2Fwidgets/merge_requests/4/notes", (
        f"the project path goes on the wire encoded as one segment: {forge.raw}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 502])
async def test_on_gitlab_a_notes_read_the_forge_refuses_raises_and_posts_nothing(status: int) -> None:
    """A failed read is never taken for "no note yet": that would post a second one, or settle nothing silently."""
    for call in (_post, _settle):
        forge = _Forge([], read=status)
        with pytest.raises(httpx.HTTPStatusError):
            if call is _post:
                await _post(GITLAB_MR, forge, human_merge_only=True)
            else:
                await _settle(GITLAB_MR, forge)
        assert forge.writes() == [], f"{call.__name__}: nothing is written after a refused read: {forge.writes()}"


@pytest.mark.asyncio
async def test_on_gitlab_a_note_the_forge_answers_with_a_5xx_is_never_posted_again() -> None:
    """GitLab may have stored a note it answered 502 for: posting it again would leave two."""
    forge = _Forge([], write=502)
    with pytest.raises(httpx.HTTPStatusError):
        await _post(GITLAB_MR, forge, human_merge_only=False)
    assert [m for m, _, _ in forge.writes()] == ["POST"], f"one POST, no retry: {forge.writes()}"
