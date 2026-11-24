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
from embry0.execution import forge_client
from embry0.execution.github_client import GitHubClient
from embry0.execution.gitlab_client import GitLabClient

CHOSEN = "chosen-000000000001"


def _refused() -> type[Exception]:
    refused = getattr(forge_client, "ChosenTokenRefused", None)
    if refused is None:
        pytest.fail("a client built with a chosen token refuses no call for another owner's repository")
    return refused


SIBLING = "sibling-000000000002"


@pytest.fixture(autouse=True)
def _a_sibling_owners_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """``my-org``'s variable, which ``my_org`` reads as well."""
    monkeypatch.setenv("GITHUB_TOKEN__MY_ORG", SIBLING)
    monkeypatch.setenv("GITLAB_TOKEN__MY_GROUP", SIBLING)


def test_a_client_with_a_chosen_token_sends_it_for_a_call_naming_its_owner_s_repository() -> None:
    chosen = GitHubClient(default_token=CHOSEN, owner_env_tokens=False, token_owner="my_org")
    assert chosen.headers("my_org/app")["Authorization"] == f"Bearer {CHOSEN}"
    assert chosen.headers("My_Org/app")["Authorization"] == f"Bearer {CHOSEN}", "an owner's name, case aside"
    assert chosen.headers(None)["Authorization"] == f"Bearer {CHOSEN}"
    lab = GitLabClient(default_token=CHOSEN, owner_env_tokens=False, token_owner="my_group")
    assert lab.headers("my_group/sub/app")["PRIVATE-TOKEN"] == CHOSEN


@pytest.mark.parametrize(
    ("client", "repo"),
    [
        (lambda: GitHubClient(default_token=CHOSEN, owner_env_tokens=False, token_owner="acme"), "my_org/app"),
        (lambda: GitHubClient(default_token=CHOSEN, owner_env_tokens=False, token_owner="my-org"), "my_org/app"),
        (lambda: GitHubClient(default_token=CHOSEN, owner_env_tokens=False, token_owner="acme"), "acme-corp/app"),
        (lambda: GitHubClient(default_token=CHOSEN, owner_env_tokens=False), "acme/app"),
        (lambda: GitLabClient(default_token=CHOSEN, owner_env_tokens=False, token_owner="grp/sub"), "grp/other/app"),
        (lambda: GitLabClient(default_token=CHOSEN, owner_env_tokens=False), "my_group/app"),
    ],
)
def test_a_client_with_a_chosen_token_refuses_a_call_for_another_owner_s_repository(client: Any, repo: str) -> None:
    """The token is its owner's to use: a call for another owner's repository, or any repository for a token chosen
    for no owner (a credential being verified, a listing's own token), is refused before anything is sent."""
    with pytest.raises(_refused()) as refused:
        client().headers(repo)
    assert refused.value.repo == repo, refused.value


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
        client = GitHubClient(default_token=CHOSEN, owner_env_tokens=False, token_owner="my_org", http_client=http)
        await client.get("/repos/my_org/app/hooks", repo="my_org/app")
        with pytest.raises(_refused()):
            await client.get("/repos/acme/app/hooks", repo="acme/app")
    assert sent == [f"Bearer {CHOSEN}"], f"a refused call was sent: {sent}"


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
    ("api/v1/organizations.py", "make_pull_request_client"),
    ("services/organizations.py", "probe_github"),
    ("services/organizations.py", "probe_gitlab"),
}
#: Those of them whose token is an organization's: each names the owner, so a call for another owner's repository is
#: refused. The others (a credential being verified, a listing's own token) are chosen for no owner.
OWNER_SITES = {
    ("api/v1/org_actions.py", "_forge_client"),
    ("api/v1/organizations.py", "run_discovery"),
    ("api/v1/organizations.py", "make_forge_client"),
    ("api/v1/organizations.py", "make_pull_request_client"),
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
    owned = {
        (rel, fn) for rel, fn, call in found if _says(call, "owner_env_tokens", False) and _says(call, "token_owner")
    }
    assert owned == OWNER_SITES, f"chosen-token clients that name the owner their token is for: {sorted(owned)}"
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


# ---- a path naming another owner --------------------------------------------------------------------------------------

_PATHS = [
    # (forge, the owner the token is for, path, sent or refused)
    ("github", "acme", "/repos/acme/app/hooks", "sent"),
    ("github", "acme", "/orgs/acme/repos", "sent"),
    ("github", "acme", "/users/Acme", "sent"),
    ("github", "acme", "/user", "sent"),
    ("github", "acme", "/repos/other/app/hooks", "refused"),
    ("github", "acme", "/repos/acme-corp/app", "refused"),
    ("github", "acme", "/orgs/other", "refused"),
    ("github", "acme", "https://api.github.com/repos/other/app/contents?page=2", "refused"),
    ("github", None, "/user", "sent"),
    ("github", None, "/repos/acme/app", "refused"),
    ("gitlab", "grp", "/projects/grp%2Fapp", "sent"),
    ("gitlab", "grp", "/groups/grp/projects", "sent"),
    ("gitlab", "grp", "/projects/1234", "sent"),
    ("gitlab", "grp", "/projects/other%2Fapp", "refused"),
    ("gitlab", "grp", "/groups/other", "refused"),
    ("gitlab", "grp/sub", "/projects/grp%2Fother%2Fapp", "refused"),
    ("gitlab", "grp", "https://gitlab.example.com/api/v4/projects/other%2Fapp/hooks", "refused"),
    ("gitlab", None, "/projects/grp%2Fapp", "refused"),
]


@pytest.mark.parametrize(("forge", "owner", "path", "outcome"), _PATHS)
async def test_a_chosen_token_s_call_whose_path_names_another_owner_is_refused(
    forge: str, owner: str | None, path: str, outcome: str
) -> None:
    """As a call naming another owner's repository with ``repo=``: the path alone names it, so the token stays home."""
    sent: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url.path))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        kw: dict[str, Any] = {"owner_env_tokens": False, "token_owner": owner, "http_client": http}
        client: Any = (
            GitHubClient(default_token=CHOSEN, **kw)
            if forge == "github"
            else GitLabClient(default_token=CHOSEN, gitlab_url="https://gitlab.example.com", **kw)
        )
        try:
            await client.get(path)
            got = "sent"
        except _refused():
            got = "refused"
    assert (got, len(sent)) == (outcome, 1 if outcome == "sent" else 0), f"{forge} {owner} {path}: {got}, {sent}"


async def test_a_deployment_default_client_reads_any_path_as_before() -> None:
    sent: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url.path))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        await GitHubClient(default_token=CHOSEN, http_client=http).get("/repos/other/app")
    assert sent == ["/repos/other/app"]


async def test_discovery_lists_a_repository_of_another_owner_without_reading_it() -> None:
    """A user's listing (``type=all``) can hold a repository they are a member of: discovery keeps it, marked, and
    reads nothing of it with this owner's token, which its client would refuse."""
    from embry0.services.org_discovery import scan_repository

    calls: list[str] = []

    class _Client:
        async def get(self, path: str, **_: Any) -> httpx.Response:
            calls.append(path)
            return httpx.Response(404, json={})

    facts = await scan_repository(_Client(), "acme", {"full_name": "other/app", "name": "app"}, hook_origins=())
    assert facts.facts == {"skipped": "another owner's repository"} and calls == [], (facts.facts, calls)


async def test_the_probe_does_not_read_hooks_of_another_owner_s_repository() -> None:
    from embry0.services.organizations import probe_github

    calls: list[str] = []

    class _Client:
        async def get(self, path: str, **_: Any) -> httpx.Response:
            calls.append(path)
            if path == "/user":
                return httpx.Response(200, json={"login": "acme-bot"})
            if path.startswith("/orgs/"):
                return httpx.Response(404, json={})
            if path == "/users/acme":
                return httpx.Response(200, json={"public_repos": 1})
            return httpx.Response(200, json=[{"full_name": "other/app"}])

    probe = await probe_github("acme", CHOSEN, client=_Client())
    assert not any(c.endswith("/hooks") for c in calls), calls
    assert probe.hooks is None and probe.sample_repo == "other/app", probe
