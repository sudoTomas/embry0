"""GitLab API proxy — auth injection, header allowlist, upstream mapping (#166)."""

import ssl

import pytest

from embry0.execution.proxy.gitlab_proxy import (
    DEFAULT_GITLAB_API_BASE,
    _auth_headers,
    create_gitlab_proxy_app,
)
from tests.fixtures.recording_upstream import RecordingUpstream

ADMIN = "admin-secret"
# The enroll handler enforces a 32-char minimum — real bearers are
# secrets.token_urlsafe(32). Short stand-ins are rejected as bad_request.
BEARER = "sandbox-bearer-token-long-enough-to-pass-01"
BEARER_2 = "sandbox-bearer-token-long-enough-to-pass-02"

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


def _gitlab_api(*tokens: str) -> RecordingUpstream:
    """A GitLab API that counts the project requests and any request carrying one of ``tokens`` (#221)."""
    return RecordingUpstream(body=b"{}", content_type="application/json", paths={"/projects/"}, credentials=set(tokens))


def test_pat_goes_in_private_token():
    """A glpat- PAT sent as Bearer is rejected by GitLab outright."""
    assert _auth_headers("glpat-abc") == {"PRIVATE-TOKEN": "glpat-abc"}


def test_oauth_token_goes_in_authorization():
    assert _auth_headers("gloas-xyz") == {"Authorization": "Bearer gloas-xyz"}


def test_default_api_base_includes_the_v4_prefix():
    assert DEFAULT_GITLAB_API_BASE.endswith("/api/v4")


async def test_health_needs_no_bearer(aiohttp_client):
    app = create_gitlab_proxy_app("glpat-x", ADMIN)
    client = await aiohttp_client(app)
    resp = await client.get("/health")
    assert resp.status == 200
    assert (await resp.json())["proxy"] == "gitlab_api"


async def test_unenrolled_request_is_401(aiohttp_client):
    """The sandbox is untrusted; no bearer means no upstream call at all."""
    app = create_gitlab_proxy_app("glpat-x", ADMIN)
    client = await aiohttp_client(app)
    resp = await client.get("/projects/1")
    assert resp.status == 401


async def test_enrolled_request_injects_the_token_and_strips_the_bearer(aiohttp_client):
    upstream = _gitlab_api("glpat-real")
    up_client = await aiohttp_client(upstream.app())
    base = str(up_client.make_url("")).rstrip("/")

    app = create_gitlab_proxy_app("glpat-real", ADMIN, api_base=base)
    client = await aiohttp_client(app)

    enrolled = await client.post(
        "/admin/enroll",
        json={"sandbox_id": "sbx-1", "sandbox_token": BEARER, "allowed_repos": ["acme/products/fbr"]},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enrolled.status == 200

    resp = await client.get(
        "/projects/acme%2Fproducts%2Ffbr",
        headers={
            "Authorization": f"Bearer {BEARER}",
            "User-Agent": "sandbox/1.0",
            "Cookie": "session=leak-me",
        },
    )
    assert resp.status == 200
    (sent,) = upstream.seen
    # Real credential injected...
    assert sent.headers.get("PRIVATE-TOKEN") == "glpat-real"
    # ...the sandbox's own bearer never reaches upstream...
    assert sent.headers.get("Authorization") is None
    # ...allowlisted headers pass, everything else is dropped.
    assert sent.headers.get("User-Agent") == "sandbox/1.0"
    assert sent.headers.get("Cookie") is None


async def test_per_sandbox_token_overrides_the_global(aiohttp_client):
    upstream = _gitlab_api("glpat-global", "glpat-per-sandbox")
    up_client = await aiohttp_client(upstream.app())

    app = create_gitlab_proxy_app("glpat-global", ADMIN, api_base=str(up_client.make_url("")).rstrip("/"))
    client = await aiohttp_client(app)
    await client.post(
        "/admin/enroll",
        json={
            "sandbox_id": "sbx-1",
            "sandbox_token": BEARER_2,
            "github_token": "glpat-per-sandbox",
            "allowed_repos": ["acme/widgets"],
        },
        headers={"X-Admin-Token": ADMIN},
    )
    await client.get("/projects/acme%2Fwidgets", headers={"Authorization": f"Bearer {BEARER_2}"})
    (sent,) = upstream.seen
    assert sent.headers.get("PRIVATE-TOKEN") == "glpat-per-sandbox"


async def test_admin_enroll_requires_the_admin_token(aiohttp_client):
    app = create_gitlab_proxy_app("glpat-x", ADMIN)
    client = await aiohttp_client(app)
    resp = await client.post("/admin/enroll", json={"sandbox_id": "s", "sandbox_token": BEARER})
    assert resp.status in (401, 403)


@pytest.mark.parametrize("query", ["", "?state=opened&per_page=5"])
async def test_query_string_is_preserved(aiohttp_client, query):
    upstream = _gitlab_api("glpat-x")
    up_client = await aiohttp_client(upstream.app())

    app = create_gitlab_proxy_app("glpat-x", ADMIN, api_base=str(up_client.make_url("")).rstrip("/"))
    client = await aiohttp_client(app)
    await client.post(
        "/admin/enroll",
        json={"sandbox_id": "s", "sandbox_token": BEARER, "allowed_repos": ["acme/widgets"]},
        headers={"X-Admin-Token": ADMIN},
    )
    await client.get(f"/projects/acme%2Fwidgets/issues{query}", headers={"Authorization": f"Bearer {BEARER}"})
    (sent,) = upstream.seen
    assert sent.raw_path == f"/projects/acme%2Fwidgets/issues{query}"


# --- private CA (spec 1A §3.2) ----------------------------------------------


async def test_without_a_ca_the_client_uses_default_verification(aiohttp_client):
    app = create_gitlab_proxy_app("glpat-x", ADMIN)
    await aiohttp_client(app)
    assert app["ca_pem"] == ""


async def test_ca_pem_reaches_the_upstream_client(aiohttp_client):
    """The proxy is the second of the three clients that must trust the CA."""
    app = create_gitlab_proxy_app("glpat-x", ADMIN, ca_pem=CA_PEM)
    await aiohttp_client(app)
    context = app["http_client"]._transport._pool._ssl_context
    assert isinstance(context, ssl.SSLContext)
    system = len(ssl.create_default_context().get_ca_certs())
    assert len(context.get_ca_certs()) == system + 1
