"""An organization's pull requests on its own forge, and the GitHub-only readers refusing a GitLab organization
(#181)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from embry0.api.v1 import organizations as organizations_api
from embry0.execution.github_client import GitHubClient
from embry0.execution.gitlab_client import GitLabClient
from embry0.execution.pull_requests import GitHubPullRequests, GitLabPullRequests
from embry0.services.organizations import Organization


def _org(forge: str) -> Organization:
    return Organization(owner="acme", forge=forge)


#: The deployment's own GitLab and GitHub tokens: never what an organization's client sends.
ENV_TOKENS = {"gitlab_token": "key-gitlab-env-000009", "github_token": "key-github-env-000009"}


def _request(ca_bundle: str = "") -> Request:
    config = SimpleNamespace(gitlab_url="https://gitlab.example.test", gitlab_ca_bundle=ca_bundle, **ENV_TOKENS)
    app = SimpleNamespace(state=SimpleNamespace(config=config))
    return Request({"type": "http", "app": app, "method": "GET", "path": "/", "headers": [], "query_string": b""})


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    asked: list[tuple[str, str]] = []

    async def token_for(_request: Any, owner: str, forge: str, _credential_id: Any, **_: Any) -> tuple[str, str]:
        asked.append((owner, forge))
        return "key-000000000001", "test"

    monkeypatch.setattr(organizations_api, "_token_for", token_for)
    return asked


@pytest.mark.asyncio
async def test_a_gitlab_organization_s_pull_requests_are_its_gitlab_s_merge_requests(_token: list[Any]) -> None:
    pulls = await organizations_api.org_pull_requests(_request(), _org("gitlab"))
    assert isinstance(pulls, GitLabPullRequests)
    client = pulls._client  # noqa: SLF001 - which client, at which host, for which owner
    assert isinstance(client, GitLabClient) and client.gitlab_url == "https://gitlab.example.test"
    assert client._token_owner == "acme"  # noqa: SLF001
    assert _token == [("acme", "gitlab")]
    assert client.headers("acme/web").get("PRIVATE-TOKEN") == "key-000000000001", (
        "the organization's credential, not the deployment's GITLAB_TOKEN"
    )


@pytest.mark.asyncio
async def test_a_gitlab_organization_s_client_trusts_the_deployment_s_ca_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from embry0.execution import gitlab_client, tls

    loaded: list[str] = []
    built: list[str] = []
    context = object()

    def load_ca_pem(path: str) -> str:
        loaded.append(path)
        return "PEM-OF-THE-BUNDLE" if path else ""

    def build_ssl_context(pem: str) -> object:
        built.append(pem)
        return context

    monkeypatch.setattr(tls, "load_ca_pem", load_ca_pem)
    monkeypatch.setattr(gitlab_client, "build_ssl_context", build_ssl_context)
    pulls = await organizations_api.org_pull_requests(_request("/run/certs/gitlab.pem"), _org("gitlab"))
    assert (loaded, built) == (["/run/certs/gitlab.pem"], ["PEM-OF-THE-BUNDLE"])
    assert pulls._client._verify is context  # noqa: SLF001


@pytest.mark.asyncio
async def test_a_github_organization_s_pull_requests_are_github_s() -> None:
    pulls = await organizations_api.org_pull_requests(_request(), _org("github"))
    assert isinstance(pulls, GitHubPullRequests) and isinstance(pulls._client, GitHubClient)  # noqa: SLF001
    assert pulls._client.headers("acme/web").get("Authorization") == "Bearer key-000000000001"  # noqa: SLF001


@pytest.mark.asyncio
async def test_the_github_reader_refuses_a_gitlab_organization_before_reading_its_credential(
    _token: list[Any],
) -> None:
    """The webhook routes, the context audit and the proposals read with GitHub's REST paths: a GitLab organization
    is refused by name (422), not sent to GitHub with its GitLab credential."""
    with pytest.raises(HTTPException) as refused:
        await organizations_api.org_forge_client(_request(), _org("gitlab"))
    assert (refused.value.status_code, refused.value.detail) == (
        422,
        "reading an organization's repositories is not supported for this forge (gitlab)",
    )
    with pytest.raises(HTTPException):
        organizations_api.make_forge_client(_request(), _org("gitlab"), "key-000000000001")
    assert _token == []
    assert isinstance(await organizations_api.org_forge_client(_request(), _org("github")), GitHubClient)
