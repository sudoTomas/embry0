"""An owner's environment token that two known owners read serves neither, with no resolver too.

An environment variable cannot spell '-' (nor '.', on GitLab), so ``my-org``
and ``my_org`` both read ``GITHUB_TOKEN__MY_ORG``. The credential resolver
reads the registered owners and serves such a variable to none of them
(#478). The lookup without a resolver (``github_tokens.resolve_for_repo`` and
its GitLab twin, reached by ``forge_token(None, …)`` and by a forge client
built with no resolver) now checks it against the owners the registry held
when last read in this process (``embry0.execution.env_token_owners``): the
orchestrator reads them at startup and the resolver records them each time it
reads them. A process that never read the registry serves an owner's
variable as before.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
from typing import Any

import httpx
import pytest
import structlog

import embry0
import embry0.api.app as app_module
from embry0.execution import forge_client, github_tokens, gitlab_tokens
from embry0.execution.github_client import GitHubClient
from embry0.execution.gitlab_client import GitLabClient
from embry0.services.credentials.resolver import CredentialResolver, forge_token

DEFAULT = "tok-default"


def _owners() -> Any:
    try:
        return importlib.import_module("embry0.execution.env_token_owners")
    except ModuleNotFoundError as exc:
        pytest.fail(f"nothing records the registered owners an env token is checked against without a resolver: {exc}")


def note_known_owners(known: dict[str, set[str]]) -> None:
    _owners().note_known_owners(known)


def _refused() -> type[Exception]:
    refused = getattr(forge_client, "ChosenTokenRefused", None)
    if refused is None:
        pytest.fail("a client built with a chosen token refuses no call for another owner's repository")
    return refused


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN__MY_ORG", "tok-my-org-env")
    monkeypatch.setenv("GITHUB_TOKEN__ACME", "tok-acme-env")
    monkeypatch.setenv("GITLAB_TOKEN__MY_GROUP", "tok-my-group-env")
    monkeypatch.setenv("GITLAB_TOKEN__GRP", "tok-grp-env")


def test_a_variable_two_known_owners_read_serves_neither_without_a_resolver() -> None:
    note_known_owners({"github": {"my-org", "my_org", "acme"}, "gitlab": {"my-group", "my.group", "grp"}})
    assert github_tokens.resolve_for_repo("my_org/app", DEFAULT) == DEFAULT
    assert github_tokens.resolve_for_repo("my-org/app", DEFAULT) == DEFAULT
    assert github_tokens.resolve_github_token("My-Org", DEFAULT) == DEFAULT, "an owner's name, case aside"
    assert gitlab_tokens.resolve_for_repo("my.group/app", DEFAULT) == DEFAULT
    assert gitlab_tokens.resolve_for_repo("my-group/sub/app", DEFAULT) == DEFAULT
    assert github_tokens.resolve_for_repo("acme/app", DEFAULT) == "tok-acme-env", "one owner's variable serves it"
    assert gitlab_tokens.resolve_for_repo("grp/app", DEFAULT) == "tok-grp-env"


def test_a_variable_one_known_owner_reads_serves_it() -> None:
    note_known_owners({"github": {"my-org", "acme"}, "gitlab": {"my_group"}})
    assert github_tokens.resolve_for_repo("my-org/app", DEFAULT) == "tok-my-org-env"
    assert gitlab_tokens.resolve_for_repo("my_group/app", DEFAULT) == "tok-my-group-env"


def test_each_forge_s_owners_are_its_own() -> None:
    """GitHub's ``my-org`` and GitLab's ``my_org`` are owners on two forges: neither shares the other's variable."""
    note_known_owners({"github": {"my-org"}, "gitlab": {"my_org", "my_group"}})
    assert github_tokens.resolve_for_repo("my-org/app", DEFAULT) == "tok-my-org-env"


def test_a_process_that_never_read_the_registry_serves_an_owner_s_variable_as_before() -> None:
    assert github_tokens.resolve_for_repo("my_org/app", DEFAULT) == "tok-my-org-env"
    assert gitlab_tokens.resolve_for_repo("my.group/app", DEFAULT) == "tok-my-group-env"


def test_a_shared_variable_is_said_once() -> None:
    note_known_owners({"github": {"my-org", "my_org"}})
    with structlog.testing.capture_logs() as logs:
        for _ in range(3):
            github_tokens.resolve_for_repo("my_org/app", DEFAULT)
    said = [e for e in logs if e["event"] == "credential_env_key_shared"]
    assert [(e["env_key"], e["owners"]) for e in said] == [("GITHUB_TOKEN__MY_ORG", ["my-org", "my_org"])], said


async def test_forge_token_with_no_resolver_and_a_client_with_no_resolver_serve_the_default() -> None:
    note_known_owners({"github": {"my-org", "my_org"}, "gitlab": {"my-group", "my_group"}})
    assert await forge_token(None, "my_org/app", "github", DEFAULT) == DEFAULT
    assert await forge_token(None, "my_group/app", "gitlab", DEFAULT) == DEFAULT
    assert GitHubClient(default_token=DEFAULT).headers("my_org/app")["Authorization"] == f"Bearer {DEFAULT}"
    assert GitLabClient(default_token=DEFAULT).headers("my_group/app")["PRIVATE-TOKEN"] == DEFAULT


class _Credentials:
    """The registry's owners, for the resolver."""

    def __init__(self, known: dict[str, set[str]]) -> None:
        self.known = known

    async def known_owners(self) -> dict[str, set[str]]:
        return self.known


async def test_the_resolver_records_the_owners_it_reads() -> None:
    resolver = CredentialResolver(_Credentials({"github": {"my-org", "my_org"}}), None, object())  # type: ignore[arg-type]
    assert await resolver.env_key_readers("my-org", "github") == ["my-org", "my_org"]
    assert _owners().shared_with("github", "my-org", github_tokens.env_key) == ["my_org"]
    assert github_tokens.resolve_for_repo("my-org/app", DEFAULT) == DEFAULT


def test_the_orchestrator_reads_the_registered_owners_at_startup() -> None:
    source = pathlib.Path(app_module.__file__).read_text()
    calls = [
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "note_known_owners"
    ]
    assert calls, "app.py never records the registered owners an env token is checked against"
    assert "known_owners()" in ast.unparse(calls[0]), ast.unparse(calls[0])


# ---- the reviewer's table: which token each request carries ----------------------------------------------------------

_TABLE = [
    # (client, path, repo, what it sends)
    ("chosen for acme", "github", "/repos/acme/app", None, "Bearer tok-chosen-for-acme"),
    ("chosen for acme", "github", "/repos/acme/app", "acme/app", "Bearer tok-chosen-for-acme"),
    ("chosen for acme", "github", "/repos/my_org/app", "my_org/app", "refused"),
    ("chosen for acme", "gitlab", "/projects/1", "grp/app", "refused"),
    ("default, no resolver", "github", "/repos/acme/app", None, "Bearer tok-chosen-for-acme"),
    ("default, no resolver", "github", "/repos/acme/app", "acme/app", "Bearer tok-acme-env"),
    ("default, no resolver", "github", "/repos/my_org/app", "my_org/app", "Bearer tok-chosen-for-acme"),
    ("default, no resolver", "gitlab", "/projects/1", "grp/app", "tok-grp-env"),
]


@pytest.mark.parametrize(("kind", "forge", "path", "repo", "sends"), _TABLE)
async def test_each_request_carries_the_token_it_should(
    kind: str, forge: str, path: str, repo: Any, sends: str
) -> None:
    """With ``my-org`` and ``my_org`` both registered, so ``GITHUB_TOKEN__MY_ORG`` is shared."""
    note_known_owners({"github": {"acme", "my-org", "my_org"}, "gitlab": {"grp"}})
    sent: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("authorization") or request.headers.get("private-token") or "none")
        return httpx.Response(200, json={})

    chosen: dict[str, Any] = {"owner_env_tokens": False, "token_owner": "acme"} if kind.startswith("chosen") else {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        client: Any = (
            GitHubClient(default_token="tok-chosen-for-acme", http_client=http, **chosen)
            if forge == "github"
            else GitLabClient(
                default_token="tok-chosen-for-acme", gitlab_url="https://gitlab.example.com", http_client=http, **chosen
            )
        )
        refused = _refused() if sends == "refused" else ()
        try:
            await (client.get(path, repo=repo) if repo else client.get(path))
        except refused:
            sent.append("refused")
    assert sent == [sends], f"{kind}, {forge}, repo={repo}: {sent}"


def test_the_lookup_without_a_resolver_reads_the_check_in_both_forges() -> None:
    """Pinned against a rewrite that would bypass it: each forge's env lookup asks ``serves``."""
    for module in (github_tokens, gitlab_tokens):
        tree = ast.parse(pathlib.Path(module.__file__).read_text())
        asked = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "serves"]
        assert asked, f"{module.__name__} serves an owner's variable without asking whether another owner reads it"
    assert pathlib.Path(embry0.__file__).parent.joinpath("execution", "env_token_owners.py").exists()
