"""POST /repos/bootstrap checks a repository on its own forge (#31).

The route verified every repository through the GitHub API, whatever forge the registry records for it: a GitLab
project read as "not found", or was checked against an unrelated GitHub repository of the same path. It now asks the
repository's registered forge, and a forge that cannot answer is an outage (502, #565 and #577's rules), not "not
found".
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

pytestmark = pytest.mark.asyncio

_REAL_CLIENT = httpx.AsyncClient
GITLAB_REPO = "group/sub/project"
GITLAB_PATH = "/api/v4/projects/group%2Fsub%2Fproject"


async def _no_wait(_seconds: float) -> None:
    return None


def _forge(monkeypatch: pytest.MonkeyPatch, answer: Callable[[httpx.Request], httpx.Response]) -> list[str]:
    """Every request a forge client makes goes to ``answer``; the requests, as host and raw path, are returned."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.host}{request.url.raw_path.decode().split('?')[0]}")
        return answer(request)

    def client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _REAL_CLIENT(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", client)
    # A retry's wait, so an outage the client retries is answered at once.
    monkeypatch.setattr("embry0.services.bootstrap._sleep", _no_wait, raising=False)
    return seen


async def _register(api_client: httpx.AsyncClient, repo: str, forge: str) -> None:
    response = await api_client.post("/api/v1/repos", json={"repo": repo, "forge": forge})
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(("visibility", "private"), [("internal", True), ("private", True), ("public", False)])
async def test_a_gitlab_repository_is_checked_on_gitlab(api_client, monkeypatch, visibility, private) -> None:
    """An internal project, which only the instance's signed-in users see, reads as private, as GitHub reads its own
    internal repositories."""
    await _register(api_client, GITLAB_REPO, "gitlab")
    monkeypatch.setenv("GITLAB_TOKEN__GROUP", "gitlab-test-token")
    seen = _forge(
        monkeypatch, lambda r: httpx.Response(200, json={"default_branch": "trunk", "visibility": visibility})
    )
    response = await api_client.post("/api/v1/repos/bootstrap", json={"repo": GITLAB_REPO})
    assert seen == [f"gitlab.com{GITLAB_PATH}"], f"the check went to {seen}, not the repository's GitLab project"
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["default_branch"], body["private"]) == ("trunk", private), body
    assert any(step.startswith("GitLab connection verified") for step in body["steps"]), body["steps"]


async def test_a_gitlab_repository_with_no_gitlab_token_says_which_token(api_client, monkeypatch) -> None:
    await _register(api_client, GITLAB_REPO, "gitlab")
    for name in ("GITLAB_TOKEN", "GITLAB_TOKEN__GROUP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN__GROUP", "github-test-token")
    seen = _forge(monkeypatch, lambda r: httpx.Response(200, json={"default_branch": "main", "private": False}))
    response = await api_client.post("/api/v1/repos/bootstrap", json={"repo": GITLAB_REPO})
    assert response.status_code == 400, response.text
    assert response.json()["detail"].startswith(f"no GitLab token configured for {GITLAB_REPO!r}"), response.text
    assert "GITLAB_TOKEN__<GROUP>" in response.json()["detail"], response.text
    assert seen == [], f"a forge was asked with no GitLab token: {seen}"


async def test_a_github_repository_is_still_checked_on_github(api_client, monkeypatch) -> None:
    await _register(api_client, "acme/widgets", "github")
    monkeypatch.setenv("GITHUB_TOKEN__ACME", "github-test-token")
    seen = _forge(monkeypatch, lambda r: httpx.Response(200, json={"default_branch": "main", "private": False}))
    response = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "acme/widgets"})
    assert seen == ["api.github.com/repos/acme/widgets"], seen
    assert response.status_code == 200, response.text
    assert (response.json()["default_branch"], response.json()["private"]) == ("main", False)


@pytest.mark.parametrize(("forge", "repo"), [("gitlab", GITLAB_REPO), ("github", "acme/widgets")])
async def test_a_repository_its_forge_does_not_know_is_not_found_there(api_client, monkeypatch, forge, repo) -> None:
    await _register(api_client, repo, forge)
    monkeypatch.setenv("GITLAB_TOKEN__GROUP", "gitlab-test-token")
    monkeypatch.setenv("GITHUB_TOKEN__ACME", "github-test-token")
    _forge(monkeypatch, lambda r: httpx.Response(404, json={"message": "404 Project Not Found"}))
    response = await api_client.post("/api/v1/repos/bootstrap", json={"repo": repo})
    name = {"github": "GitHub", "gitlab": "GitLab"}[forge]
    assert response.status_code == 400, response.text
    assert response.json()["detail"].startswith(f"{name} reports {repo!r} not found"), response.json()["detail"]
    assert (await api_client.get(f"/api/v1/repos/{repo}/-/qa-config")).status_code == 404, "nothing is stored"


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


OUTAGES = {
    "not-reached": (_refused, "could not be reached"),
    "server-error": (lambda r: httpx.Response(503, text="maintenance"), "could not answer (503)"),
    "rate-limited": (lambda r: httpx.Response(429, text="slow down"), "rate limited the request (429)"),
    "rate-limited-403": (
        lambda r: httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, text="rate limit exceeded"),
        "rate limited the request (403)",
    ),
}


EVERY_FORGE = ("not-reached", "rate-limited", "server-error")


@pytest.mark.parametrize(
    ("forge", "repo", "outage"),
    [
        *[("gitlab", GITLAB_REPO, outage) for outage in EVERY_FORGE],
        *[("github", "acme/widgets", outage) for outage in EVERY_FORGE],
        # GitHub sends its primary rate limit as a 403 with no requests left (#577); GitLab sends a 429.
        ("github", "acme/widgets", "rate-limited-403"),
    ],
)
async def test_a_forge_that_cannot_answer_is_an_outage_not_a_missing_repository(
    api_client, monkeypatch, forge, repo, outage
) -> None:
    """#565 and #577's rules: a forge not reached, a server error or a rate limit is a 502 naming what the forge did,
    after the client's own retries; nothing says the repository is missing, and nothing is stored."""
    await _register(api_client, repo, forge)
    monkeypatch.setenv("GITLAB_TOKEN__GROUP", "gitlab-test-token")
    monkeypatch.setenv("GITHUB_TOKEN__ACME", "github-test-token")
    answer, says = OUTAGES[outage]
    _forge(monkeypatch, answer)
    response = await api_client.post("/api/v1/repos/bootstrap", json={"repo": repo})
    name = {"github": "GitHub", "gitlab": "GitLab"}[forge]
    assert response.status_code == 502, f"{response.status_code}: {response.text}"
    assert response.json()["detail"] == f"Checking {repo!r}: {name} {says}", response.json()["detail"]
    assert (await api_client.get(f"/api/v1/repos/{repo}/-/qa-config")).status_code == 404, "nothing is stored"


async def test_an_unregistered_repository_on_a_deployment_that_names_no_default_forge_asks_no_forge(
    api_client, monkeypatch
) -> None:
    """With DEFAULT_FORGE empty an unregistered repository has no forge to be checked on: the route says to register
    it, and asks neither GitHub nor GitLab about a path that may be another's there."""
    monkeypatch.setattr(api_client.app.state.repos_repo, "_default_forge", "")
    monkeypatch.setenv("GITHUB_TOKEN__ACME", "github-test-token")
    seen = _forge(monkeypatch, lambda r: httpx.Response(200, json={"default_branch": "main", "private": False}))
    response = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "acme/unknown"})
    assert seen == [], f"a forge was asked about an unregistered repository: {seen}"
    assert response.status_code == 400, response.text
    assert "is not registered" in response.json()["detail"], response.json()["detail"]
