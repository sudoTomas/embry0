"""The route table reads full path templates on every supported FastAPI (#502).

FastAPI 0.137 keeps included routers as opaque entries in ``app.routes`` and
leaves each included route's own ``path`` without the include prefix. These
tests pin that the table and ``route_template`` still answer with the full
template, which the merge-scope allowlist, the metrics exclusions and every
route-walking test depend on.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from embry0.api.app import create_app
from embry0.api.deps import _admin_dependency, _auth_dependency, _operator_dependency
from embry0.api.route_table import route_template
from tests.fixtures.routes import ServedRoute, app_routes


def _without_convertors(path: str) -> str:
    """``{full_repo:path}`` as the OpenAPI schema spells it: ``{full_repo}``."""
    return re.sub(r"\{(\w+):[^}]+\}", r"{\1}", path)


def test_the_table_has_every_operation_of_the_openapi_schema() -> None:
    """FastAPI's own schema is an independent list of full templates."""
    app = create_app()
    schema = {(method.upper(), path) for path, operations in app.openapi()["paths"].items() for method in operations}
    table = {
        (method, _without_convertors(route.path))
        for route in app_routes(app)
        if route.is_api_route
        for method in route.methods or ()
    }
    missing = sorted(schema - table)
    assert not missing, f"the route table lacks operations the OpenAPI schema lists: {missing}"


def test_a_websocket_route_of_an_included_router_keeps_its_path() -> None:
    paths = {route.path: route.methods for route in app_routes(create_app())}
    assert "/ws/jobs/{job_id}/events" in paths, (
        "the job-events WebSocket route is missing from the route table: "
        f"{sorted(p for p in paths if p.startswith('/ws'))}"
    )
    assert paths["/ws/jobs/{job_id}/events"] is None


def test_a_route_carries_the_dependencies_of_its_include() -> None:
    """``users`` gets ``require_admin`` from ``include_router``, not from its routes."""
    routes = [route for route in app_routes(create_app()) if route.path.startswith("/api/v1/users")]
    assert routes, "no /api/v1/users route in the route table"
    for route in routes:
        assert route.dependant is not None
        calls = {d.call for d in route.dependant.dependencies}
        assert _admin_dependency in calls, f"{route.path} lost the admin gate it is included with"
    jobs = next(route for route in app_routes(create_app()) if route.path == "/api/v1/jobs/{job_id}")
    assert jobs.dependant is not None
    assert _auth_dependency in {d.call for d in jobs.dependant.dependencies}


def _gates(route: ServedRoute) -> set[object]:
    assert route.dependant is not None
    return {d.call for d in route.dependant.dependencies}


def test_every_skill_write_is_admin_and_the_skill_reads_are_not() -> None:
    """Skill sources are code the agents load, so every write is admin tier (spec 3.4)."""
    routes = [route for route in app_routes(create_app()) if route.is_api_route]
    writes = {
        (method, route.path): route
        for route in routes
        for method in route.methods or ()
        if method in {"POST", "PUT", "PATCH", "DELETE"}
        and (route.path.startswith("/api/v1/skills") or route.path.startswith("/api/v1/agents/{agent_type}/skills"))
    }
    assert sorted(writes) == [
        ("DELETE", "/api/v1/agents/{agent_type}/skills/{skill_name}"),
        ("DELETE", "/api/v1/skills/inline/{name}"),
        ("DELETE", "/api/v1/skills/sources/{source_id}"),
        ("PATCH", "/api/v1/skills/sources/{source_id}"),
        ("POST", "/api/v1/agents/{agent_type}/skills/{skill_name}"),
        ("POST", "/api/v1/skills/sources"),
        ("POST", "/api/v1/skills/sources/{source_id}/sync"),
        ("PUT", "/api/v1/skills/inline/{name}"),
    ], "the set of skill write routes changed; gate any new one as admin and list it here"
    ungated = sorted(key for key, route in writes.items() if _admin_dependency not in _gates(route))
    assert ungated == [], f"skill write routes without the admin gate: {ungated}"

    reads = {"/api/v1/skills", "/api/v1/skills/sources", "/api/v1/tools"}
    for route in routes:
        if route.path in reads and "GET" in (route.methods or ()):
            gates = _gates(route)
            assert _admin_dependency not in gates, f"GET {route.path} must be readable without admin"
            assert _auth_dependency in gates, f"GET {route.path} must still need an authenticated caller"
            reads.discard(route.path)
    assert reads == set(), f"read routes missing from the table: {reads}"


def test_the_stored_session_is_operator_tier_and_the_verification_row_read_tier() -> None:
    """A transcript is what the agent saw in the sandbox, scrubbed after the fact (game UI spec 3.6)."""
    routes = {route.path: route for route in app_routes(create_app()) if route.is_api_route}
    session = routes["/api/v1/jobs/{job_id}/agents/{agent_type}/session"]
    assert _operator_dependency in _gates(session), "GET .../session must carry the operator gate"
    verification = routes["/api/v1/jobs/{job_id}/verification"]
    assert _operator_dependency not in _gates(verification), "GET .../verification stays read tier"
    assert _auth_dependency in _gates(verification)


def _template_app() -> FastAPI:
    inner = APIRouter(prefix="/inner")

    @inner.get("/things/{thing_id}")
    def thing(thing_id: str, request: Request) -> dict[str, str]:
        return {"template": route_template(request)}

    outer = APIRouter()
    outer.include_router(inner, prefix="/v1")
    app = FastAPI()
    app.include_router(outer, prefix="/api")
    return app


def test_the_template_has_every_include_prefix() -> None:
    body = TestClient(_template_app()).get("/api/v1/inner/things/7").json()
    assert body == {"template": "/api/v1/inner/things/{thing_id}"}, f"route_template lost an include prefix: {body}"


def test_one_router_included_twice_answers_with_the_prefix_that_served() -> None:
    router = APIRouter()

    @router.get("/items/{item_id}")
    def item(item_id: str, request: Request) -> dict[str, str]:
        return {"template": route_template(request)}

    app = FastAPI()
    app.include_router(router, prefix="/a")
    app.include_router(router, prefix="/b")
    client = TestClient(app)
    for prefix in ("/a", "/b"):
        body = client.get(f"{prefix}/items/1").json()
        assert body == {"template": f"{prefix}/items/{{item_id}}"}, f"GET {prefix}/items/1 got {body}"
