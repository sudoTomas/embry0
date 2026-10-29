"""The ``handler`` label stays bounded: a route template, never a literal ID (#507).

prometheus-fastapi-instrumentator 8.x labels a request that Starlette answers
with a trailing-slash redirect by its raw path (7.x used the template), so
``/api/v1/jobs/<uuid>/`` would mint one series per ID. The wiring excludes
exactly those redirects; these tests pin that, and pin that the exclusion
reaches nothing else.
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client.parser import text_string_to_metric_families

from embry0.api.app import create_app
from embry0.config import Embry0Config
from tests.fixtures.routes import app_routes

_JOB_A = "0a1b2c3d-0000-4000-8000-00000000000a"
_JOB_B = "0a1b2c3d-0000-4000-8000-00000000000b"
_JOB_C = "0a1b2c3d-0000-4000-8000-00000000000c"


def _filled(path: str, token: str) -> str:
    """``path`` with every parameter set to ``token``; a repository is ``token/token``, two segments (#243)."""
    return re.sub(r"\{[^}]+\}", lambda m: f"{token}/{token}" if m.group(0).endswith(":repo}") else token, path)


@asynccontextmanager
async def _no_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


def _app() -> FastAPI:
    config = Embry0Config(
        _env_file=None,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        # The sweep below sends one request per route; a 429 would hide the redirect.
        api_rate_limit_per_minute=1_000_000,
        webhook_rate_limit_per_minute=1_000_000,
    )
    return create_app(config, lifespan_override=_no_lifespan)


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        # No lifespan, so handlers that reach for app.state answer 500; that
        # still records a sample, and the labels are all these tests read.
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )


async def _request_samples(client: AsyncClient) -> list[dict[str, str]]:
    body = (await client.get("/metrics")).text
    return [
        dict(sample.labels)
        for family in text_string_to_metric_families(body)
        if family.name == "http_requests"
        for sample in family.samples
        if sample.name == "http_requests_total"
    ]


def test_no_route_path_ends_in_a_slash() -> None:
    """The exclusion assumes a handler ending in ``/`` is never a real template.

    A route declared with a trailing slash would make that false: its own
    template could then match a sibling route's exclusion pattern and drop
    out of /metrics without anyone noticing.
    """
    slashed = [route.path for route in app_routes(_app()) if route.path.endswith("/")]
    assert slashed == [], f"routes ending in '/' would break the #507 metrics exclusion: {slashed}"


async def test_a_trailing_slash_redirect_never_puts_an_id_in_the_handler_label() -> None:
    app = _app()
    async with _client(app) as client:
        for job_id in (_JOB_A, _JOB_B):
            response = await client.get(f"/api/v1/jobs/{job_id}/")
            assert response.status_code == 307, "precondition: Starlette redirects the trailing slash"
        samples = await _request_samples(client)

    leaked = sorted({s["handler"] for s in samples if _JOB_A in s["handler"] or _JOB_B in s["handler"]})
    assert leaked == [], f"a trailing-slash redirect was labelled with its raw path (unbounded cardinality): {leaked}"


async def test_a_templated_request_is_still_counted_under_its_template() -> None:
    app = _app()
    async with _client(app) as client:
        await client.get(f"/api/v1/jobs/{_JOB_C}/")  # excluded
        await client.get(f"/api/v1/jobs/{_JOB_C}")  # must still count
        samples = await _request_samples(client)

    handlers = {(s["handler"], s["method"]) for s in samples}
    assert ("/api/v1/jobs/{job_id}", "GET") in handlers, (
        f"the slash-redirect exclusion over-matched: GET /api/v1/jobs/{{job_id}} is missing from {sorted(handlers)}"
    )


async def test_an_untemplated_404_ending_in_a_slash_is_still_counted_as_none() -> None:
    app = _app()
    async with _client(app) as client:
        response = await client.get("/no/such/route/")
        assert response.status_code == 404
        samples = await _request_samples(client)

    nones = [s for s in samples if s["handler"] == "none" and s["method"] == "GET" and s["status"] == "4xx"]
    assert nones, (
        "an untemplated 404 whose path ends in '/' is no longer counted under handler='none' "
        f"(the exclusion is too broad): {sorted({s['handler'] for s in samples})}"
    )


async def test_no_parametrised_route_leaks_a_literal_value_through_its_slash_redirect() -> None:
    """Every parametrised HTTP route create_app() registers, not just /jobs.

    Catches a route added after the metrics are installed, which the
    exclusion list (built at install time) would not know about.
    """
    app = _app()
    tokens: list[str] = []
    redirected = 0
    async with _client(app) as client:
        for n, route in enumerate(r for r in app_routes(app) if r.is_api_route and "{" in r.path):
            token = f"lit{n}x"
            tokens.append(token)
            path = _filled(route.path, token) + "/"
            method = sorted((route.methods or set()) - {"HEAD"})[0]
            response = await client.request(method, path)
            redirected += response.status_code == 307
        samples = await _request_samples(client)

    assert redirected > 0, "precondition: no request was answered with a trailing-slash redirect"
    leaked = sorted({s["handler"] for s in samples if any(t in s["handler"] for t in tokens)})
    assert leaked == [], f"slash redirects labelled with their raw path: {leaked}"


async def test_every_route_is_counted_under_its_full_template() -> None:
    """The ``handler`` label is the template with its include prefix (#502).

    Alerts and dashboards select on these labels. FastAPI 0.137 dropped the
    prefix from the route a request records; the instrumentator has to
    rebuild it, and a label that lost it would leave those queries empty.
    """
    app = _app()
    expected: set[tuple[str, str]] = set()
    async with _client(app) as client:
        for n, route in enumerate(r for r in app_routes(app) if r.is_api_route and r.path != "/metrics"):
            for method in sorted((route.methods or set()) - {"HEAD"}):
                expected.add((route.path, method))
                await client.request(method, _filled(route.path, f"lit{n}x"))
        samples = await _request_samples(client)

    missing = sorted(expected - {(s["handler"], s["method"]) for s in samples})
    assert not missing, f"requests not counted under their full route template: {missing}"
