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

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client.parser import text_string_to_metric_families

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.metrics.wiring import _slash_redirect_patterns
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


def _templates_the_exclusion_drops(app: FastAPI) -> list[str]:
    """Route templates an exclusion pattern matches: their requests would drop out of /metrics unseen."""
    patterns = [re.compile(pattern) for pattern in _slash_redirect_patterns(app)]
    return sorted({route.path for route in app_routes(app) if any(p.match(route.path) for p in patterns)})


def test_no_route_template_is_excluded_from_the_metrics() -> None:
    """The exclusion assumes a handler it matches is a redirect, never a real template.

    A route declared with a trailing slash can make that false: its own
    template can match a sibling route's exclusion pattern (``/items/{id}/``
    against the pattern of ``/items/{id}``) and drop out of /metrics without
    anyone noticing. A repository's own address after the separator,
    ``/repos/{full_repo:repo}/-/`` (#257), ends in a slash and matches none: no
    repository segment can be ``{``.
    """
    dropped = _templates_the_exclusion_drops(_app())
    assert dropped == [], f"route templates the #507 metrics exclusion would drop: {dropped}"


def test_the_template_check_fires_on_a_slash_route_a_sibling_excludes() -> None:
    app = _app()
    planted = APIRouter()
    planted.add_api_route("/planted/{item_id}", lambda: None, methods=["GET"])
    planted.add_api_route("/planted/{item_id}/", lambda: None, methods=["GET"])
    app.include_router(planted, prefix="/api/v1")
    assert _templates_the_exclusion_drops(app) == ["/api/v1/planted/{item_id}/"]


def _slash_routes_without_a_twin(app: FastAPI) -> list[str]:
    """Parametrised routes ending in ``/`` whose path without the slash is not served for the same methods.

    Starlette answers that path with a redirect to the slash, the instrumentator labels the redirect by its raw
    path, and the exclusion covers only the other direction: one series per value, whatever the convertor.
    """
    methods: dict[str, set[str]] = {}
    for route in app_routes(app):
        if route.methods:
            methods.setdefault(route.path, set()).update(route.methods)
    return sorted(
        path
        for path, served in methods.items()
        if "{" in path and path.endswith("/") and methods.get(path.removesuffix("/"), set()) != served
    )


def test_every_route_ending_in_a_slash_also_serves_its_path_without_it() -> None:
    """Structural, so a typed convertor the request sweep cannot fill (``{n:int}``, ``{u:uuid}``) is covered too."""
    lonely = _slash_routes_without_a_twin(_app())
    assert lonely == [], f"routes ending in '/' whose slashless path Starlette would redirect: {lonely}"


def test_the_twin_check_fires_on_slash_routes_with_typed_convertors() -> None:
    app = _app()
    planted = APIRouter()
    planted.add_api_route("/planted/{n:int}/", lambda: None, methods=["GET"])
    planted.add_api_route("/planted/{u:uuid}/", lambda: None, methods=["GET"])
    planted.add_api_route("/twinned/{u:uuid}/", lambda: None, methods=["GET", "DELETE"])
    planted.add_api_route("/twinned/{u:uuid}", lambda: None, methods=["GET"])
    app.include_router(planted, prefix="/api/v1")
    assert _slash_routes_without_a_twin(app) == [
        "/api/v1/planted/{n:int}/",
        "/api/v1/planted/{u:uuid}/",
        "/api/v1/twinned/{u:uuid}/",
    ]


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
    exclusion list (built at install time) would not know about. A route
    whose path ends in ``/`` is asked without it too: Starlette redirects that
    the other way, which the exclusion does not cover, so such a route must
    also serve the path without the slash (``/repos/{full_repo:repo}/-``, #257).
    """
    app = _app()
    tokens: list[str] = []
    redirected = 0
    async with _client(app) as client:
        for n, route in enumerate(r for r in app_routes(app) if r.is_api_route and "{" in r.path):
            token = f"lit{n}x"
            tokens.append(token)
            filled = _filled(route.path, token)
            method = sorted((route.methods or set()) - {"HEAD"})[0]
            for path in (filled + "/", filled.removesuffix("/")) if filled.endswith("/") else (filled + "/",):
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
