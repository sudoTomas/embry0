"""Tests for /api/v1/github/repos."""

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from embry0.api.app import create_app
from embry0.api.v1 import github as github_module
from embry0.config import Embry0Config
from embry0.services.credentials.resolver import CredentialResolver
from tests.unit.api.v1.test_credentials import FakeSecrets, MemRepo


@pytest.fixture(autouse=True)
def _clean_owner_tokens(monkeypatch):
    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)


@pytest.fixture
def app_with_token():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="ghp_test_token")
    return create_app(config)


@pytest.fixture
def app_without_token():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="")
    return create_app(config)


@pytest.mark.asyncio
async def test_list_repos_returns_400_when_token_missing(app_without_token):
    transport = ASGITransport(app=app_without_token)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/github/repos")
    assert resp.status_code == 400
    assert "No GitHub token configured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_repos_returns_repos_from_github(app_with_token):
    """Mocks httpx.AsyncClient so we don't actually hit api.github.com."""
    fake_repos = [
        {
            "full_name": "example-org/embry0",
            "description": "Self-hosted issue-to-PR orchestrator.",
            "private": True,
            "html_url": "https://github.com/example-org/embry0",
            "default_branch": "main",
            "language": "Python",
            "open_issues_count": 3,
        },
        {
            "full_name": "example-org/some-other",
            "description": None,
            "private": False,
            "html_url": "https://github.com/example-org/some-other",
            "default_branch": "main",
            "language": None,
            "open_issues_count": 0,
        },
    ]
    mock_resp = MagicMock()
    mock_resp.json = MagicMock(return_value=fake_repos)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.links = {}

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("embry0.api.v1.github.httpx.AsyncClient", return_value=mock_client):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/github/repos")

    assert resp.status_code == 200
    body = resp.json()
    assert "repos" in body
    assert len(body["repos"]) == 2
    assert body["repos"][0]["full_name"] == "example-org/embry0"
    assert body["repos"][0]["language"] == "Python"
    assert body["repos"][1]["language"] is None
    # Verify the GitHub API was called with the right params
    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args
    assert "/user/repos" in call_kwargs[0][0]
    assert call_kwargs[1]["params"]["per_page"] == 100
    assert "owner" in call_kwargs[1]["params"]["affiliation"]


@pytest.mark.asyncio
async def test_list_repos_answers_502_naming_github_s_status_when_it_refuses_every_account(app_with_token):
    """#568: GitHub's status was passed on as embry0's own, so every token expired answered 401, which the dashboard
    takes for a bad embry0 key and answers with the sign-in prompt. GitHub refusing every account is a 502 that
    names GitHub's status."""
    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json = MagicMock(return_value={"message": "Bad credentials"})
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=mock_resp)
    )

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("embry0.api.v1.github.httpx.AsyncClient", return_value=mock_client):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/github/repos")

    assert (resp.status_code, resp.json()["detail"]) == (502, "GitHub API returned 401 for every account"), (
        f"GitHub refusing every account is answered as an upstream failure, not the caller's: {resp.status_code}"
    )


def _repo(full_name: str) -> dict:
    return {
        "full_name": full_name,
        "description": None,
        "private": True,
        "html_url": f"https://github.com/{full_name}",
        "default_branch": "main",
        "language": "Python",
        "open_issues_count": 0,
    }


class _FakeResponse:
    def __init__(self, payload, status_code=200, links=None):
        self._payload = payload
        self.status_code = status_code
        self.links = links or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err",
                request=httpx.Request("GET", "https://api.github.com/user/repos"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


#: The answer of an account that never answers.
_HANG = object()


class _FakeClient:
    """Maps Authorization header -> canned response, mimicking httpx.AsyncClient.

    A token may map to a LIST of responses — consumed sequentially per client
    instance, one per ``get`` — to fake Link-header pagination. Repeats the
    last entry when the pagination loop calls past the end (the cap tests).
    """

    responses_by_token: dict[str, object] = {}
    instantiations = 0

    def __init__(self, headers=None, timeout=None):
        type(self).instantiations += 1
        self._token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        self._page = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None):
        value = self.responses_by_token[self._token]
        if isinstance(value, BaseException):
            raise value
        if value is _HANG:
            # An account that never answers; the listing's deadline cancels it.
            await asyncio.Event().wait()
        if isinstance(value, list):
            resp = value[min(self._page, len(value) - 1)]
            self._page += 1
            return resp
        return value


@pytest.mark.asyncio
async def test_list_repos_merges_and_dedups_across_tokens(monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {
        "tok-default": _FakeResponse([_repo("octo-org/embry0"), _repo("shared/overlap")]),
        "tok-rc": _FakeResponse([_repo("acme-corp/widgets"), _repo("shared/overlap")]),
    }
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "tok-rc")
    result = await github_module._fetch_all_repos("tok-default", per_page=100, sort="updated")
    names = [r.full_name for r in result]
    assert names == ["acme-corp/widgets", "octo-org/embry0", "shared/overlap"]


@pytest.mark.asyncio
async def test_list_repos_skips_failing_token(monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {
        "tok-dead": _FakeResponse({"message": "Bad credentials"}, status_code=401),
        "tok-rc": _FakeResponse([_repo("acme-corp/widgets")]),
    }
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "tok-rc")
    result = await github_module._fetch_all_repos("tok-dead", per_page=100, sort="updated")
    assert [r.full_name for r in result] == ["acme-corp/widgets"]


@pytest.mark.asyncio
async def test_list_repos_all_tokens_failing_raises(monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {"tok-dead": _FakeResponse({"message": "no"}, status_code=401)}
    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)
    with pytest.raises(Exception):
        await github_module._fetch_all_repos("tok-dead", per_page=100, sort="updated")


# ── RAV-1301: pagination, discovery, cache ─────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_discovery_cache():
    github_module._DISCOVERY_CACHE["at"] = 0.0
    github_module._DISCOVERY_CACHE["by_name"] = {}
    github_module._DISCOVERY_CACHE["truncated"] = []
    if "failed" in github_module._DISCOVERY_CACHE:
        github_module._DISCOVERY_CACHE["failed"] = []
    _FakeClient.instantiations = 0
    yield


def _next_link():
    return {"next": {"url": "https://api.github.com/user/repos?page=2"}}


@pytest.mark.asyncio
async def test_pagination_follows_next_link_and_merges(monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {
        "tok-default": [
            _FakeResponse([_repo("octo-org/page-one")], links=_next_link()),
            _FakeResponse([_repo("octo-org/page-two")]),
        ],
    }
    result = await github_module._fetch_all_repos("tok-default", per_page=100, sort="updated")
    assert [r.full_name for r in result] == ["octo-org/page-one", "octo-org/page-two"]


@pytest.mark.asyncio
async def test_pagination_stops_at_cap_and_reports_truncation(monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {
        "tok-default": _FakeResponse([_repo("octo-org/looped")], links=_next_link()),
    }
    raw, truncated = await github_module._fetch_repos_for_token(
        "GITHUB_TOKEN", "tok-default", per_page=100, sort="updated"
    )
    assert truncated is True
    assert len(raw) == github_module.MAX_PAGES_PER_TOKEN


def _stub_repos_repo(app, registered):
    repos_repo = MagicMock()
    repos_repo.list_all = AsyncMock(return_value=[{"repo": r} for r in registered])
    app.state.repos_repo = repos_repo
    return repos_repo


def _perm_repo(full_name: str, push=None) -> dict:
    r = _repo(full_name)
    if push is not None:
        r["permissions"] = {"admin": False, "push": push, "pull": True}
    return r


@pytest.mark.asyncio
async def test_discoverable_repos_subtracts_registered_and_annotates(app_with_token, monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "tok-rc")
    _FakeClient.responses_by_token = {
        "ghp_test_token": _FakeResponse(
            [_perm_repo("octo-org/registered", push=True), _perm_repo("octo-org/new", push=True)]
        ),
        "tok-rc": _FakeResponse([_perm_repo("acme-corp/no-push", push=False), _repo("acme-corp/no-perms")]),
    }
    _stub_repos_repo(app_with_token, ["octo-org/registered"])

    transport = ASGITransport(app=app_with_token)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/github/discoverable-repos")

    assert resp.status_code == 200
    body = resp.json()
    by_name = {r["full_name"]: r for r in body["repos"]}
    assert sorted(by_name) == ["acme-corp/no-perms", "acme-corp/no-push", "octo-org/new"]
    assert by_name["octo-org/new"]["token_env_key"] == "GITHUB_TOKEN"
    assert by_name["acme-corp/no-push"]["token_env_key"] == "GITHUB_TOKEN__ACME_CORP"
    assert by_name["acme-corp/no-push"]["push"] is False
    assert by_name["octo-org/new"]["push"] is True
    assert by_name["acme-corp/no-perms"]["push"] is None
    assert body["truncated_env_keys"] == []
    assert body["cached"] is False
    # Secrecy: token values must never appear in the response body.
    assert "tok-rc" not in resp.text
    assert "ghp_test_token" not in resp.text


@pytest.mark.asyncio
async def test_discoverable_repos_reports_truncated_env_key(app_with_token, monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {
        "ghp_test_token": _FakeResponse([_repo("octo-org/looped")], links=_next_link()),
    }
    _stub_repos_repo(app_with_token, [])

    transport = ASGITransport(app=app_with_token)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/github/discoverable-repos")

    assert resp.status_code == 200
    assert resp.json()["truncated_env_keys"] == ["GITHUB_TOKEN"]


@pytest.mark.asyncio
async def test_discoverable_repos_caches_fanout_but_not_registry(app_with_token, monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {
        "ghp_test_token": _FakeResponse([_repo("octo-org/a"), _repo("octo-org/b")]),
    }
    repos_repo = _stub_repos_repo(app_with_token, [])

    transport = ASGITransport(app=app_with_token)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/v1/github/discoverable-repos")
        # Registering between calls must take effect immediately despite the cache.
        repos_repo.list_all = AsyncMock(return_value=[{"repo": "octo-org/a"}])
        second = await client.get("/api/v1/github/discoverable-repos")

    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert _FakeClient.instantiations == 1  # one fan-out client total
    assert [r["full_name"] for r in second.json()["repos"]] == ["octo-org/b"]


@pytest.mark.asyncio
async def test_discoverable_repos_refresh_bypasses_cache(app_with_token, monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {
        "ghp_test_token": _FakeResponse([_repo("octo-org/a")]),
    }
    _stub_repos_repo(app_with_token, [])

    transport = ASGITransport(app=app_with_token)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/api/v1/github/discoverable-repos")
        resp = await client.get("/api/v1/github/discoverable-repos?refresh=true")

    assert resp.json()["cached"] is False
    assert _FakeClient.instantiations == 2


@pytest.fixture
def app_with_registry():
    config = Embry0Config(
        _env_file=None,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        github_token="ghp_env_placeholder",
    )
    app = create_app(config)
    app.state.credentials_repo = MemRepo()
    app.state.secrets_provider = FakeSecrets()
    app.state.credential_resolver = CredentialResolver(app.state.credentials_repo, FakeSecrets(), config, environ={})
    _stub_repos_repo(app, [])
    return app


@pytest.fixture
async def client(app_with_registry):
    transport = ASGITransport(app=app_with_registry)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _add_github_credential(app, label: str, secret: str) -> None:
    await app.state.credentials_repo.create(
        kind="forge_token",
        provider="github",
        host="github.com",
        label=label,
        secret_ciphertext="enc:" + secret,
        masked_preview="ghp_••••",
        created_by="test",
    )


@pytest.mark.asyncio
async def test_discoverable_repos_carry_credential_id(app_with_registry, client, monkeypatch):
    # registry credential "alice" (token ghp_alice_placeholder) sees acme/widgets; env GITHUB_TOKEN sees acme/gadgets
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    await _add_github_credential(app_with_registry, "alice", "ghp_alice_placeholder")
    _FakeClient.responses_by_token = {
        "ghp_alice_placeholder": _FakeResponse([_repo("acme/widgets")]),
        "ghp_env_placeholder": _FakeResponse([_repo("acme/gadgets")]),
    }
    resp = await client.get("/api/v1/github/discoverable-repos")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_name = {r["full_name"]: r for r in body["repos"]}
    assert by_name["acme/widgets"]["credential_id"] == "cred-000000000001"
    assert by_name["acme/widgets"]["credential_label"] == "alice"
    assert by_name["acme/widgets"]["token_env_key"] == ""
    assert by_name["acme/gadgets"]["credential_id"] is None
    assert by_name["acme/gadgets"]["token_env_key"] == "GITHUB_TOKEN"
    assert "ghp_alice_placeholder" not in resp.text
    assert "ghp_env_placeholder" not in resp.text
    # The fan-out cache outlives the request. It keeps attribution, not the secret.
    assert "ghp_alice_placeholder" not in str(github_module._DISCOVERY_CACHE)
    assert "ghp_env_placeholder" not in str(github_module._DISCOVERY_CACHE)


@pytest.mark.asyncio
async def test_one_failing_credential_is_skipped_and_reported_by_label(app_with_registry, client, monkeypatch):
    # credential "revoked" → 401 from the fake; "alice" → 200
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    app_with_registry.state.config.github_token = ""
    await _add_github_credential(app_with_registry, "alice", "ghp_alice_placeholder")
    await _add_github_credential(app_with_registry, "revoked", "ghp_revoked_placeholder")
    _FakeClient.responses_by_token = {
        "ghp_alice_placeholder": _FakeResponse([_repo("acme/widgets")]),
        "ghp_revoked_placeholder": _FakeResponse({"message": "Bad credentials"}, status_code=401),
    }
    resp = await client.get("/api/v1/github/discoverable-repos")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["failed"] == ["revoked"]
    assert "acme/widgets" in {r["full_name"] for r in body["repos"]}
    assert "ghp_revoked_placeholder" not in resp.text
    assert "ghp_alice_placeholder" not in resp.text
    assert "ghp_revoked_placeholder" not in str(github_module._DISCOVERY_CACHE)
    assert "ghp_alice_placeholder" not in str(github_module._DISCOVERY_CACHE)


async def _answer(app, path: str) -> httpx.Response:
    """What a caller is answered: an exception the app does not handle is its 500, not raised into the test."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.get(path)


_UNREACHABLE = {
    "refused": httpx.ConnectError("connection refused"),
    "timeout": httpx.ReadTimeout("timed out"),
}


@pytest.mark.parametrize("error", list(_UNREACHABLE), ids=list(_UNREACHABLE))
@pytest.mark.asyncio
async def test_an_account_github_never_answers_is_reported_by_label(app_with_registry, monkeypatch, error):
    """#340: one account's listing that got no answer (a refused connection, a timeout) failed the whole request
    with a 500; it is reported like one GitHub refused, by label, and the other accounts' repositories are listed."""
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    app_with_registry.state.config.github_token = ""
    await _add_github_credential(app_with_registry, "alice", "ghp_alice_placeholder")
    await _add_github_credential(app_with_registry, "offline", "ghp_offline_placeholder")
    _FakeClient.responses_by_token = {
        "ghp_alice_placeholder": _FakeResponse([_repo("acme/widgets")]),
        "ghp_offline_placeholder": _UNREACHABLE[error],
    }
    with capture_logs() as logs:
        resp = await _answer(app_with_registry, "/api/v1/github/discoverable-repos")
    assert resp.status_code == 200, f"one account's {error} fails the whole listing: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["failed"] == ["offline"], f"the account that could not be listed is named: {body['failed']}"
    assert [r["full_name"] for r in body["repos"]] == ["acme/widgets"], body["repos"]
    (logged,) = [e for e in logs if e["event"] == "github_repos_fetch_failed"]
    assert (logged["label"], logged["status"], logged["error"]) == (
        "offline",
        None,
        type(_UNREACHABLE[error]).__name__,
    ), logged


@pytest.mark.asyncio
async def test_no_account_github_answers_is_a_502(app_with_registry, monkeypatch):
    """Every account unreachable: nothing to list, and GitHub's own status is not there to pass on."""
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    app_with_registry.state.config.github_token = ""
    await _add_github_credential(app_with_registry, "offline", "ghp_offline_placeholder")
    _FakeClient.responses_by_token = {"ghp_offline_placeholder": httpx.ConnectError("connection refused")}
    resp = await _answer(app_with_registry, "/api/v1/github/discoverable-repos")
    assert (resp.status_code, resp.json().get("detail")) == (502, "GitHub could not be reached"), (
        f"no account reachable answers 502: {resp.status_code} {resp.text}"
    )


def _short_deadline(monkeypatch) -> None:
    """The listing's deadline, a fraction of a second here. A tree without one has nothing to shorten, and the test
    then shows that it waits on the hanging accounts."""
    try:
        import embry0.api.forge_listings as forge_listings
    except ImportError:
        return
    monkeypatch.setattr(forge_listings, "DEADLINE_SECONDS", 0.2)


@pytest.mark.asyncio
async def test_hanging_accounts_end_at_the_deadline_and_are_reported_by_label(app_with_registry, monkeypatch):
    """#340 review: the accounts were read one after another, each request with a 30 s timeout over up to five
    pages, so four hanging accounts held the request two minutes, past a proxy's limit, and the person got a
    gateway timeout instead of the accounts that answered. They are read at once, within one deadline."""
    _short_deadline(monkeypatch)
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    app_with_registry.state.config.github_token = ""
    await _add_github_credential(app_with_registry, "alice", "ghp_alice_placeholder")
    for n in range(4):
        await _add_github_credential(app_with_registry, f"slow-{n}", f"ghp_slow{n}_placeholder")
    _FakeClient.responses_by_token = {
        "ghp_alice_placeholder": _FakeResponse([_repo("acme/widgets")]),
        **{f"ghp_slow{n}_placeholder": _HANG for n in range(4)},
    }
    started = time.monotonic()
    try:
        with capture_logs() as logs:
            resp = await asyncio.wait_for(_answer(app_with_registry, "/api/v1/github/discoverable-repos"), timeout=5)
    except TimeoutError:
        pytest.fail("the listing waited on the hanging accounts past 5 s instead of its deadline")
    took = time.monotonic() - started
    assert resp.status_code == 200 and took < 3, f"answered at the deadline: {resp.status_code} after {took:.2f}s"
    body = resp.json()
    assert sorted(body["failed"]) == [f"slow-{n}" for n in range(4)], f"each hanging account is named: {body}"
    assert [r["full_name"] for r in body["repos"]] == ["acme/widgets"], body["repos"]
    errors = {e["label"]: e["error"] for e in logs if e["event"] == "github_repos_fetch_failed"}
    assert errors == {f"slow-{n}": "PastDeadline" for n in range(4)}, errors


def _short_account_deadline(monkeypatch) -> None:
    """Each account's own deadline, and the listing's, a fraction of a second here."""
    try:
        import embry0.api.forge_listings as forge_listings
    except ImportError:
        return
    monkeypatch.setattr(forge_listings, "DEADLINE_SECONDS", 2.0)
    monkeypatch.setattr(forge_listings, "ACCOUNT_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(forge_listings, "CONCURRENCY", 4)


@pytest.mark.asyncio
async def test_good_accounts_behind_hanging_ones_are_still_listed(app_with_registry, monkeypatch):
    """#340 review: four hanging accounts listed first held all four slots until the listing's deadline, so the four
    good accounts behind them never started, and the answer was a 502 that no account could be reached."""
    _short_account_deadline(monkeypatch)
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    app_with_registry.state.config.github_token = ""
    # Discovery reads the registry's credentials in the order of their labels: the hanging ones first.
    for n in range(4):
        await _add_github_credential(app_with_registry, f"a-slow-{n}", f"ghp_slow{n}_placeholder")
    for n in range(4):
        await _add_github_credential(app_with_registry, f"b-good-{n}", f"ghp_good{n}_placeholder")
    _FakeClient.responses_by_token = {
        **{f"ghp_slow{n}_placeholder": _HANG for n in range(4)},
        **{f"ghp_good{n}_placeholder": _FakeResponse([_repo(f"acme/app-{n}")]) for n in range(4)},
    }
    try:
        resp = await asyncio.wait_for(_answer(app_with_registry, "/api/v1/github/discoverable-repos"), timeout=10)
    except TimeoutError:
        pytest.fail("the listing waited on the hanging accounts past 10 s")
    assert resp.status_code == 200, f"the good accounts behind hanging ones: {resp.status_code} {resp.text}"
    body = resp.json()
    assert [r["full_name"] for r in body["repos"]] == [f"acme/app-{n}" for n in range(4)], body
    assert body["failed"] == [f"a-slow-{n}" for n in range(4)], body["failed"]


@pytest.mark.asyncio
async def test_a_mix_of_refused_and_unreachable_accounts_is_worded_as_such(app_with_registry, monkeypatch):
    """#340 review: one account refused and one unreachable was reported as GitHub returning 401 for every account."""
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    app_with_registry.state.config.github_token = ""
    await _add_github_credential(app_with_registry, "revoked", "ghp_revoked_placeholder")
    await _add_github_credential(app_with_registry, "offline", "ghp_offline_placeholder")
    _FakeClient.responses_by_token = {
        "ghp_revoked_placeholder": _FakeResponse({"message": "Bad credentials"}, status_code=401),
        "ghp_offline_placeholder": httpx.ConnectError("connection refused"),
    }
    resp = await _answer(app_with_registry, "/api/v1/github/discoverable-repos")
    assert (resp.status_code, resp.json().get("detail")) == (
        502,
        "GitHub listed no account: refused 1 (401) and could not be reached for 1",
    ), f"the 502 says what GitHub did with each account, counted: {resp.text}"


@pytest.mark.asyncio
async def test_an_answer_missing_an_unreachable_account_is_not_kept(app_with_registry, monkeypatch):
    """#340 review: an answer without an account that could not be reached was cached for five minutes, so the
    account stayed missing after it came back. It is served once; the next read asks every account again."""
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    app_with_registry.state.config.github_token = ""
    await _add_github_credential(app_with_registry, "alice", "ghp_alice_placeholder")
    await _add_github_credential(app_with_registry, "offline", "ghp_offline_placeholder")
    _FakeClient.responses_by_token = {
        "ghp_alice_placeholder": _FakeResponse([_repo("acme/widgets")]),
        "ghp_offline_placeholder": httpx.ConnectError("connection refused"),
    }
    first = await _answer(app_with_registry, "/api/v1/github/discoverable-repos")
    assert first.status_code == 200 and first.json()["failed"] == ["offline"], first.text
    _FakeClient.responses_by_token["ghp_offline_placeholder"] = _FakeResponse([_repo("acme/gadgets")])
    second = await _answer(app_with_registry, "/api/v1/github/discoverable-repos")
    body = second.json()
    assert (body["cached"], body["failed"], sorted(r["full_name"] for r in body["repos"])) == (
        False,
        [],
        ["acme/gadgets", "acme/widgets"],
    ), f"the next read asks the account that was missing: {body}"
