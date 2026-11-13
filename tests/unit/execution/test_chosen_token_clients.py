"""A forge client built with a token its caller chose sends that token on every call.

A client with no credential resolver reads, for a call that names a
repository, the owner's environment token (``GITHUB_TOKEN__<OWNER>``,
``GITLAB_TOKEN__<GROUP>``) in place of its default token. Such a variable is
not one owner's alone: ``my-org`` and ``my_org`` read ``GITHUB_TOKEN__MY_ORG``.
A client built with a chosen token (a credential being probed, the account an
organization's discovery or actions run with, one listing's own token) is
built with ``owner_env_tokens=False`` and never swaps it; every other client
in embry0 is built with the credential resolver, which serves a shared
variable to no owner. A guard reads embry0's source so a new client is one or
the other.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

import embry0
from embry0.execution.github_client import GitHubClient
from embry0.execution.gitlab_client import GitLabClient

CHOSEN = "chosen-000000000001"
SIBLING = "sibling-000000000002"


@pytest.fixture(autouse=True)
def _a_sibling_owners_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """``my-org``'s variable, which ``my_org`` reads as well."""
    monkeypatch.setenv("GITHUB_TOKEN__MY_ORG", SIBLING)
    monkeypatch.setenv("GITLAB_TOKEN__MY_GROUP", SIBLING)


def test_a_client_with_a_chosen_token_sends_it_for_a_call_naming_a_repository() -> None:
    chosen = GitHubClient(default_token=CHOSEN, owner_env_tokens=False)
    assert chosen.headers("my_org/app")["Authorization"] == f"Bearer {CHOSEN}"
    assert chosen.headers(None)["Authorization"] == f"Bearer {CHOSEN}"
    lab = GitLabClient(default_token=CHOSEN, owner_env_tokens=False)
    assert lab.headers("my_group/app")["PRIVATE-TOKEN"] == CHOSEN


def test_a_client_built_with_the_deployment_default_still_reads_the_owners_variable() -> None:
    """Unchanged: without a resolver, the default token is the deployment's, and a call naming a repository reads
    its owner's variable, as it did."""
    assert GitHubClient(default_token=CHOSEN).headers("my_org/app")["Authorization"] == f"Bearer {SIBLING}"
    assert GitLabClient(default_token=CHOSEN).headers("my_group/app")["PRIVATE-TOKEN"] == SIBLING


async def test_a_request_through_a_client_with_a_chosen_token_carries_it() -> None:
    sent: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        client = GitHubClient(default_token=CHOSEN, owner_env_tokens=False, http_client=http)
        await client.get("/repos/my_org/app/hooks", repo="my_org/app")
    assert sent == [f"Bearer {CHOSEN}"], sent


# ---- every client in embry0 is one or the other ---------------------------------------------------------------------

_SRC = pathlib.Path(embry0.__file__).parent

#: The functions that build a client with a token their caller chose: each must say so.
CHOSEN_TOKEN_SITES = {
    ("api/v1/credentials.py", "_probe_login"),
    ("api/v1/github.py", "_headers"),
    ("api/v1/gitlab.py", "_projects_for_entries"),
    ("api/v1/org_actions.py", "_forge_client"),
    ("api/v1/organizations.py", "run_discovery"),
    ("api/v1/organizations.py", "make_forge_client"),
    ("services/organizations.py", "probe_github"),
    ("services/organizations.py", "probe_gitlab"),
}
#: Clients built with neither, each with why.
NEITHER = {
    ("api/v1/github.py", "_fetch_repos_for_token"): "builds a URL only; the request carries _headers'",
    ("notifications/qa_comment.py", "upsert_sticky_comment"): (
        "only when no client is passed; every production caller passes one built with the resolver"
    ),
}


def _clients() -> Iterator[tuple[str, str, ast.Call]]:
    for path in sorted(_SRC.rglob("*.py")):
        rel = str(path.relative_to(_SRC))
        if rel.startswith("execution/") and path.name in {"github_client.py", "gitlab_client.py", "forge_client.py"}:
            continue
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call):
                    name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                    if name in {"GitHubClient", "GitLabClient"}:
                        yield rel, function.name, node


def _says(call: ast.Call, keyword: str, value: Any = None) -> bool:
    for kw in call.keywords:
        if kw.arg == keyword:
            return value is None or (isinstance(kw.value, ast.Constant) and kw.value.value is value)
    return False


def test_every_forge_client_is_built_with_the_resolver_or_says_its_token_was_chosen() -> None:
    found = list(_clients())
    assert len(found) > 20, f"only {len(found)} clients found: the walk measures nothing"
    loose = [
        f"{rel}:{call.lineno} in {fn}"
        for rel, fn, call in found
        if not _says(call, "credential_resolver")
        and not _says(call, "owner_env_tokens", False)
        and (rel, fn) not in NEITHER
    ]
    assert loose == [], (
        f"forge clients built with neither the credential resolver nor owner_env_tokens=False: {loose}; without a "
        "resolver a call naming a repository reads its owner's environment variable, which another owner may share"
    )
    chosen = {(rel, fn) for rel, fn, call in found if _says(call, "owner_env_tokens", False)}
    assert chosen == CHOSEN_TOKEN_SITES, f"clients built with a chosen token: {sorted(chosen)}"
    assert {(rel, fn) for rel, fn, _call in found} >= set(NEITHER), "an allowed exception no longer builds a client"


def test_no_forge_token_lookup_is_passed_no_resolver() -> None:
    """``forge_token`` runs the environment lookup only when it is given no resolver."""
    bare = []
    for path in sorted(_SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "forge_token":
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and first.value is None:
                    bare.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert bare == [], f"forge_token called with no resolver: {bare}"
