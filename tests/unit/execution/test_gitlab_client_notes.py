"""Every note and diff discussion the GitLab client writes carries words, never a quick action."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from embry0.execution.gitlab_client import GitLabClient

ROOT = Path(__file__).resolve().parents[3]
#: A note whose words would merge the merge request and approve it, were GitLab to run them.
BODY = "<!-- marker -->\nLooks good.\n/approve\n  /merge\n\r/close"
NEUTRAL = "<!-- marker -->\nLooks good.\n\u200b/approve\n  \u200b/merge\n\r\u200b/close"


def _client(existing_note: bool = False) -> tuple[GitLabClient, list[tuple[str, Any]]]:
    sent: list[tuple[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            notes = [{"id": 5, "body": "<!-- marker --> old"}] if existing_note else []
            return httpx.Response(200, json=notes)
        sent.append((request.method, json.loads(request.content)))
        return httpx.Response(201, json={"id": 6})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitLabClient(default_token="key-000000000001", http_client=http), sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("write", "existing_note"),
    [
        (lambda c: c.create_issue_note("acme/web", 1, BODY), False),
        (lambda c: c.upsert_sticky_issue_note("acme/web", 1, BODY, marker="<!-- marker -->"), False),
        (lambda c: c.upsert_sticky_issue_note("acme/web", 1, BODY, marker="<!-- marker -->"), True),
        (lambda c: c.upsert_sticky_mr_note("acme/web", 1, BODY, marker="<!-- marker -->"), False),
        (lambda c: c.upsert_sticky_mr_note("acme/web", 1, BODY, marker="<!-- marker -->"), True),
        (lambda c: c.edit_sticky_mr_note("acme/web", 1, BODY, marker="<!-- marker -->"), True),
        (lambda c: c.post_mr_note("acme/web", 1, BODY), False),
        (lambda c: c.create_mr_note("acme/web", 1, BODY), False),
        (lambda c: c.create_mr_discussion("acme/web", 1, body=BODY, position={"new_line": 3}), False),
    ],
    ids=[
        "issue-note",
        "sticky-issue-note-new",
        "sticky-issue-note-edited",
        "sticky-mr-note-new",
        "sticky-mr-note-edited",
        "sticky-mr-note-edit",
        "mr-note",
        "mr-note-bool",
        "mr-discussion",
    ],
)
async def test_every_note_is_written_without_a_quick_action(write: Any, existing_note: bool) -> None:
    client, sent = _client(existing_note)
    await write(client)
    assert [body["body"] for _method, body in sent] == [NEUTRAL]


#: A write's path that reaches a GitLab project's issues, merge requests, notes or discussions, as its source reads.
_GITLAB_WRITE_PATH = ("project_path(", "/projects/", "/notes", "/discussions", "merge_requests")
_WRITES = {"post", "put", "patch", "request"}


def _writes(tree: ast.AST) -> list[tuple[ast.Call, str]]:
    """Each ``<x>.post/put/patch/request(...)`` call, and the function it is in."""
    found: list[tuple[ast.Call, str]] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _WRITES:
                found.append((node, function.name))
    return found


def test_the_gitlab_client_writes_only_through_send_words() -> None:
    """A writer added later that posts, puts or patches by itself fails here, whatever key or payload it sends."""
    tree = ast.parse((ROOT / "embry0/execution/gitlab_client.py").read_text())
    loose = [
        f"line {call.lineno} in {function}: {ast.unparse(call.func)}"
        for call, function in _writes(tree)
        if function != "send_words" and isinstance(call.func.value, ast.Name) and call.func.value.id == "self"
    ]
    assert loose == [], f"GitLab client writes outside send_words: {loose}"


def test_no_module_writes_to_a_gitlab_project_but_through_send_words() -> None:
    """Every write whose path reaches a project's issues, merge requests, notes or discussions, in any module
    (gitlab_mrs, pull_requests, ...), is ``send_words``: a raw post, put, patch or request there fails here."""
    loose = []
    reads = 0
    for path in sorted((ROOT / "embry0").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for call, function in _writes(tree):
            reads += 1
            target = ast.unparse(call.args[0]) if call.args else ""
            if call.func.attr == "request" and len(call.args) > 1:
                target = ast.unparse(call.args[1])
            if function == "send_words" or not any(marker in target for marker in _GITLAB_WRITE_PATH):
                continue
            loose.append(f"{path.relative_to(ROOT)}:{call.lineno} in {function}: {call.func.attr}({target})")
    assert reads > 50, f"only {reads} writes read: the walk measures nothing"
    assert loose == [], f"writes to a GitLab project not through send_words: {loose}"


@pytest.mark.asyncio
async def test_send_words_sends_every_words_field_without_quick_actions() -> None:
    """Each field GitLab reads quick actions in, under whatever key, and nothing else."""
    client, sent = _client()
    payload = {"body": BODY, "note": "/approve", "description": "x\n\r/draft", "title": "/kept", "position": {"a": 1}}
    await client.send_words(
        "POST", client.project_path("acme/web", "/merge_requests/1/notes"), repo="acme/web", json=payload
    )
    assert sent == [
        (
            "POST",
            {
                "body": NEUTRAL,
                "note": "\u200b/approve",
                "description": "x\n\r\u200b/draft",
                "title": "/kept",
                "position": {"a": 1},
            },
        )
    ]


@pytest.mark.asyncio
async def test_a_merge_request_s_description_carries_no_quick_action() -> None:
    """The description a merge request opens with carries the developer's own summary: a /target_branch or /draft in
    it would retarget or draft the merge request GitLab is creating."""
    from embry0.services.gitlab_mrs import ensure_mr_for_branch

    created: list[dict[str, Any]] = []

    def forge(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/merge_requests"):
            return httpx.Response(200, json=[])
        if request.method == "GET":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.method == "POST" and request.url.path.endswith("/merge_requests"):
            created.append(json.loads(request.content))
            return httpx.Response(
                201, json={"iid": 9, "web_url": "https://gitlab.example.test/acme/web/-/merge_requests/9"}
            )
        return httpx.Response(200, json={})

    client = GitLabClient(
        default_token="key-000000000001",
        gitlab_url="https://gitlab.example.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(forge)),
    )
    summary = "Fixed the sender.\n/target_branch release-2.0\n/label ~deploy-now\n\r/draft"
    url = await ensure_mr_for_branch(
        repo="acme/web", branch="embry0/fix-sender", title="Fix the sender", body=summary, client=client
    )
    assert url and created, created
    description = created[0]["description"]
    assert not any(line.lstrip(" \t\r").startswith("/") for line in description.replace("\r", "\n").splitlines()), (
        description
    )
    assert created[0]["target_branch"] == "main"
