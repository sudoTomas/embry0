"""GitLabClient — auth header, project encoding, self-managed hosts (#164)."""

import ssl

import httpx
import pytest

from embry0.execution.gitlab_client import (
    DEFAULT_GITLAB_URL,
    GitLabClient,
    api_base,
    encode_project,
    make_gitlab_client,
)
from embry0.execution.gitlab_tokens import namespace_of, resolve_for_repo

SUBGROUP_REPO = "acme/products/fbr"

# Copied from tests/unit/execution/test_tls.py: a self-signed test CA.
CA_PEM = """-----BEGIN CERTIFICATE-----
MIIDFTCCAf2gAwIBAgIUKGxb659gemN1Fu46hjP/cM50qQcwDQYJKoZIhvcNAQEL
BQAwGTEXMBUGA1UEAwwOZW1icnkwLXRlc3QtY2EwIBcNMjYwOTE4MTM1NTMxWhgP
MjEyNjA4MjUxMzU1MzFaMBkxFzAVBgNVBAMMDmVtYnJ5MC10ZXN0LWNhMIIBIjAN
BgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAps3bhaL+Zory+o1N1k2As7R5O2Ts
RAiM8Sy1gRgBWtZBoDSxsEiwj9y/mF3ktm1lIBCfC4DJC1s3l8y6Z2M6w8wU1fHJ
7FJcSyC8Xb5pW+DY16EmXs1N0Z/Sk8gaXKDxC/Eb2aWmbH82NO6lLkiXdlRLFM72
WJORS8RzLZWBSGJxegh+VzmisIFPe5pRyYw5aQ+79f4guvNhseKoN2/0vdsBEK1a
X/6zygGrh0jpeDaFmDj3mQI/N+ttg81rL5PRfwE+LCdSxgzVbmDSkyeslNDXJOOl
H6JbfgLbBb3eNOrCERngxcN0eO7GhHi5aSBASAHFs/EDnvX8cXnm3zvo/wIDAQAB
o1MwUTAdBgNVHQ4EFgQUK214K4DPd64gDs8w4/oQNCSX4V4wHwYDVR0jBBgwFoAU
K214K4DPd64gDs8w4/oQNCSX4V4wDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0B
AQsFAAOCAQEAQ65bqCRpk1rsCUb55zrmLXKJwvAhfAoUvla6DDuyTX1IYLsFpSwE
sxBPs8f8vXhg703ospwDAaRGPljF0yEuBdh0XMK2cMFDgu2aY9f3/edr5MFaj8h6
R2LrznGBf7knKJr4MPKVhlUvNXPI0g90j2ofSxQ56GHC5ozk+/6JUF3bsOgqLrle
qoRdavJt88e/4KcKCra5RIwipuNVmlCeTYxVwCCSXlccESJ39bjHD2vDDqv2ro/E
c4e0+qw4L6q4kPNiZiNmumxWekny53FXSVrhhHJS4MwcuLyfvwBD4LsvuNhKYpgm
mJ6PoFKMXR9xjyWUTmkVT6oYfPdl0vzcQw==
-----END CERTIFICATE-----
"""


@pytest.fixture(autouse=True)
def _clean_namespace_tokens(monkeypatch):
    import os

    for key in [k for k in os.environ if k.startswith("GITLAB_TOKEN__")]:
        monkeypatch.delenv(key)


def _client(handler, **kwargs) -> GitLabClient:
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GitLabClient(default_token="glpat-abc", http_client=http, sleep=_sleep, **kwargs)
    client.slept = slept  # type: ignore[attr-defined]
    return client


# --- project identity -------------------------------------------------------


def test_project_path_encodes_slashes():
    """The slashes MUST become %2F or the request addresses another route."""
    assert encode_project(SUBGROUP_REPO) == "acme%2Fproducts%2Ffbr"
    assert GitLabClient().project_path(SUBGROUP_REPO) == "/projects/acme%2Fproducts%2Ffbr"


def test_project_path_appends_a_suffix():
    assert GitLabClient().project_path("g/p", "/issues/7") == "/projects/g%2Fp/issues/7"


def test_project_encoding_survives_dots_and_dashes():
    assert encode_project("my-group/sub.group/proj.x") == "my-group%2Fsub.group%2Fproj.x"


# --- auth header ------------------------------------------------------------


def test_personal_access_token_uses_private_token():
    """A glpat- PAT sent as Bearer is rejected by GitLab."""
    headers = GitLabClient(default_token="glpat-abc").headers()
    assert headers["PRIVATE-TOKEN"] == "glpat-abc"
    assert "Authorization" not in headers


def test_oauth_token_uses_bearer():
    headers = GitLabClient(default_token="gloas-xyz").headers()
    assert headers["Authorization"] == "Bearer gloas-xyz"
    assert "PRIVATE-TOKEN" not in headers


def test_no_token_sends_no_credential():
    headers = GitLabClient(default_token="").headers()
    assert "PRIVATE-TOKEN" not in headers
    assert "Authorization" not in headers


# --- namespace token resolution ---------------------------------------------


def test_namespace_is_the_top_level_group():
    """Tokens are issued at the top-level group, not the full nested path."""
    assert namespace_of(SUBGROUP_REPO) == "acme"
    assert namespace_of("owner/repo") == "owner"
    assert namespace_of("noslash") is None
    assert namespace_of(None) is None


def test_resolves_the_per_namespace_token(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN__ACME", "glpat-ns")
    client = GitLabClient(default_token="glpat-default")
    assert client.headers(SUBGROUP_REPO)["PRIVATE-TOKEN"] == "glpat-ns"
    assert client.headers("other/proj")["PRIVATE-TOKEN"] == "glpat-default"


def test_namespace_env_key_normalises_punctuation(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN__MY_GROUP", "glpat-x")
    assert resolve_for_repo("my-group/proj", "fallback") == "glpat-x"
    assert resolve_for_repo("my.group/proj", "fallback") == "glpat-x"


# --- self-managed hosts -----------------------------------------------------


def test_api_base_defaults_to_gitlab_com():
    assert api_base() == "https://gitlab.com/api/v4"
    assert api_base(DEFAULT_GITLAB_URL) == "https://gitlab.com/api/v4"


def test_api_base_supports_a_self_managed_host():
    assert api_base("https://git.internal.example") == "https://git.internal.example/api/v4"
    assert api_base("https://git.internal.example/") == "https://git.internal.example/api/v4"


def test_api_base_falls_back_on_an_empty_url():
    assert api_base("") == "https://gitlab.com/api/v4"


def test_web_origin_is_kept_separate_from_the_api_base():
    """Human-facing links use the origin, not /api/v4."""
    client = GitLabClient(gitlab_url="https://git.internal.example/")
    assert client.gitlab_url == "https://git.internal.example"
    assert client.url("/projects/x") == "https://git.internal.example/api/v4/projects/x"


# --- requests ---------------------------------------------------------------


async def test_get_project_hits_the_encoded_path():
    seen: dict = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["private_token"] = request.headers.get("PRIVATE-TOKEN")
        return httpx.Response(200, json={"id": 1, "path_with_namespace": SUBGROUP_REPO})

    project = await _client(handler).get_project(SUBGROUP_REPO)
    assert project is not None
    assert project["path_with_namespace"] == SUBGROUP_REPO
    assert seen["url"].endswith("/api/v4/projects/acme%2Fproducts%2Ffbr")
    assert seen["private_token"] == "glpat-abc"


async def test_get_project_returns_none_when_missing():
    def handler(request):
        return httpx.Response(404, json={"message": "404 Project Not Found"})

    assert await _client(handler).get_project("no/such") is None


async def test_get_issue_uses_the_project_scoped_iid():
    def handler(request):
        assert str(request.url).endswith("/projects/g%2Fp/issues/11")
        return httpx.Response(200, json={"iid": 11, "title": "t"})

    issue = await _client(handler).get_issue("g/p", 11)
    assert issue is not None and issue["iid"] == 11


async def test_create_issue_note_posts_the_body():
    import json as json_mod

    seen: dict = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json_mod.loads(request.content)
        return httpx.Response(201, json={"id": 5})

    assert await _client(handler).create_issue_note("g/p", 11, "hello") is True
    assert seen["url"].endswith("/projects/g%2Fp/issues/11/notes")
    assert seen["body"] == {"body": "hello"}


async def test_create_issue_note_reports_failure():
    def handler(request):
        return httpx.Response(403, json={"message": "forbidden"})

    assert await _client(handler).create_issue_note("g/p", 11, "hello") is False


async def test_upsert_sticky_issue_note_creates_when_no_marked_note():
    import json as json_mod

    calls: list[tuple[str, str, dict | None]] = []

    def handler(request):
        body = json_mod.loads(request.content) if request.content else None
        calls.append((request.method, str(request.url), body))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "body": "unrelated"}])
        return httpx.Response(201, json={"id": 7})

    note_id = await _client(handler).upsert_sticky_issue_note("g/p", 11, "<!-- m -->new", marker="<!-- m -->")
    assert note_id == 7
    assert [c[0] for c in calls] == ["GET", "POST"], f"expected a lookup then a create, got {calls}"
    assert calls[1][1].endswith("/projects/g%2Fp/issues/11/notes")
    assert calls[1][2] == {"body": "<!-- m -->new"}


async def test_upsert_sticky_issue_note_updates_the_earliest_marked_note():
    import json as json_mod

    calls: list[tuple[str, str, dict | None]] = []

    def handler(request):
        body = json_mod.loads(request.content) if request.content else None
        calls.append((request.method, str(request.url), body))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 3, "body": "<!-- m -->old"}, {"id": 9, "body": "<!-- m -->dup"}])
        return httpx.Response(200, json={"id": 3})

    note_id = await _client(handler).upsert_sticky_issue_note("g/p", 11, "<!-- m -->new", marker="<!-- m -->")
    assert note_id == 3
    assert [c[0] for c in calls] == ["GET", "PUT"], f"expected a lookup then an update, got {calls}"
    assert calls[1][1].endswith("/projects/g%2Fp/issues/11/notes/3")
    assert calls[1][2] == {"body": "<!-- m -->new"}


async def test_upsert_sticky_issue_note_lists_notes_oldest_first():
    def handler(request):
        if request.method == "GET":
            assert request.url.params["sort"] == "asc", "notes must be listed oldest first"
            assert request.url.params["order_by"] == "created_at"
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 1})

    assert await _client(handler).upsert_sticky_issue_note("g/p", 11, "x", marker="m") == 1


async def test_upsert_sticky_issue_note_raises_on_rejected_write():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).upsert_sticky_issue_note("g/p", 11, "x", marker="m")


async def test_upsert_sticky_issue_note_raises_on_rejected_read_without_posting():
    methods: list[str] = []

    def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(403, json={"message": "forbidden"})
        return httpx.Response(201, json={"id": 1})

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).upsert_sticky_issue_note("g/p", 11, "x", marker="m")
    assert methods == ["GET"], f"a rejected notes read must not create a note, got {methods}"


# --- inherited transport behaviour -----------------------------------------


async def test_retry_policy_is_inherited():
    """The retry loop is ForgeClient's — GitLab must not re-implement it."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, json={})
        return httpx.Response(200, json={"id": 1})

    client = _client(handler)
    assert (await client.get("/projects/1")).status_code == 200
    assert client.slept == [3.0]


async def test_gitlab_ratelimit_headers_are_honoured():
    """GitLab sends RateLimit-* without the X- prefix GitHub uses."""
    import time

    def handler(request):
        return httpx.Response(
            429,
            headers={
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": str(int(time.time()) + 4),
            },
            json={},
        )

    client = _client(handler, max_attempts=2)
    await client.get("/projects/1")
    assert 0 < client.slept[0] <= 5


# --- repo map ---------------------------------------------------------------


def test_repo_map_defaults_to_identity():
    """A GitLab project IS the repo — unlike Linear, no mapping is required."""
    from embry0.execution.gitlab_client import parse_repo_map, resolve_target_repo

    assert parse_repo_map("") == {}
    assert resolve_target_repo(SUBGROUP_REPO, {}) == SUBGROUP_REPO


def test_repo_map_overrides_identity():
    from embry0.execution.gitlab_client import parse_repo_map, resolve_target_repo

    mapping = parse_repo_map('{"acme/products/fbr": "mirror/fbr"}')
    assert resolve_target_repo(SUBGROUP_REPO, mapping) == "mirror/fbr"
    assert resolve_target_repo("other/proj", mapping) == "other/proj"


def test_malformed_repo_map_does_not_break_startup():
    """A typo must not take the orchestrator down; identity still works."""
    from embry0.execution.gitlab_client import parse_repo_map, resolve_target_repo

    assert parse_repo_map("{not json") == {}
    assert parse_repo_map("[1, 2]") == {}
    assert resolve_target_repo(SUBGROUP_REPO, parse_repo_map("{not json")) == SUBGROUP_REPO


def test_empty_project_path_resolves_to_empty():
    from embry0.execution.gitlab_client import resolve_target_repo

    assert resolve_target_repo("", {"a": "b"}) == ""


# --- private CA (spec 1A §3.2) ----------------------------------------------


def test_no_ca_pem_leaves_verify_at_the_default():
    """The overwhelmingly common case must not build an SSLContext at all."""
    assert GitLabClient()._verify is True


def test_ca_pem_builds_a_context_over_the_system_store():
    client = GitLabClient(gitlab_url="https://gitlab.dev.example", ca_pem=CA_PEM)
    assert isinstance(client._verify, ssl.SSLContext)
    system = len(ssl.create_default_context().get_ca_certs())
    assert len(client._verify.get_ca_certs()) == system + 1


def test_explicit_verify_wins_over_ca_pem():
    """A caller that passes its own context (tests, pinning) is not overridden."""
    context = ssl.create_default_context()
    client = GitLabClient(ca_pem=CA_PEM, verify=context)
    assert client._verify is context


def test_factory_wires_token_url_and_bundle(tmp_path):
    bundle = tmp_path / "ca.pem"
    bundle.write_text(CA_PEM)

    class _Config:
        gitlab_token = "glpat-factory"
        gitlab_url = "https://gitlab.dev.example/"
        gitlab_ca_bundle = str(bundle)

    client = make_gitlab_client(_Config())
    assert client.gitlab_url == "https://gitlab.dev.example"
    assert client._base_url == "https://gitlab.dev.example/api/v4"
    assert client.headers()["PRIVATE-TOKEN"] == "glpat-factory"
    assert isinstance(client._verify, ssl.SSLContext)


def test_factory_without_a_bundle_keeps_the_default_verify():
    class _Config:
        gitlab_token = "glpat-x"
        gitlab_url = ""
        gitlab_ca_bundle = ""

    client = make_gitlab_client(_Config())
    assert client._base_url == f"{DEFAULT_GITLAB_URL}/api/v4"
    assert client._verify is True


def test_factory_propagates_extra_kwargs():
    """Call sites that inject a test transport must still be able to."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    class _Config:
        gitlab_token = "glpat-x"
        gitlab_url = ""
        gitlab_ca_bundle = ""

    client = make_gitlab_client(_Config(), http_client=http)
    assert client._http is http
