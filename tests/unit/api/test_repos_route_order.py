"""The `/repos/{full_repo:path}` catch-all must not shadow sub-resources (#162).

`repos` exposes a catch-all so GitLab nested subgroups
(`acme/products/fbr`) can be addressed at all. A catch-all matches
`/repos/acme/widgets/qa-config` just as happily as `/repos/acme/widgets`, so
registration order is the only thing keeping the sub-resource routers
reachable — and that is invisible at the call site.

This caught a live regression during #162: `repos.router` was registered
before `qa_config.router`, and `GET /repos/{o}/{r}/qa-config` silently started
returning the repo detail document instead of the config.
"""

from embry0.api.app import create_app
from embry0.api.route_table import ServedRoute
from embry0.config import Embry0Config
from tests.fixtures.routes import app_routes

# Every suffix owned by a router other than `repos`.
_SUB_RESOURCES = ("qa-config", "preferences", "environment", "credentials", "history", "undo")


def _all_routes() -> list[ServedRoute]:
    """In matching order, which is what these tests are about."""
    return app_routes(create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)))


def _routes() -> list[ServedRoute]:
    return [r for r in _all_routes() if r.path.startswith("/api/v1/repos")]


def test_catch_all_is_registered_after_every_sub_resource():
    paths = [r.path for r in _routes()]
    catch_all = next(i for i, p in enumerate(paths) if p == "/api/v1/repos/{full_repo:path}")
    for suffix in _SUB_RESOURCES:
        owners = [i for i, p in enumerate(paths) if p.endswith(f"/{suffix}")]
        assert owners, f"no route found for /{suffix} — did it move?"
        assert min(owners) < catch_all, (
            f"/{suffix} is registered after the /repos catch-all and is now unreachable. "
            "Move app.include_router(repos.router, ...) below it in create_app."
        )


def test_sub_resource_routes_accept_multi_segment_repos():
    """A two-segment {owner}/{repo} cannot express a GitLab subgroup path."""
    checked = set()
    for route in _routes():
        for suffix in _SUB_RESOURCES:
            if route.path.endswith(f"/{suffix}"):
                checked.add(suffix)
                assert "{full_repo:path}" in route.path, (
                    f"{route.path} still uses fixed segments — acme/products/fbr would 404 against it"
                )
    assert checked == set(_SUB_RESOURCES), f"no /repos route found for {sorted(set(_SUB_RESOURCES) - checked)}"


def test_literal_credentials_routes_precede_the_id_routes():
    """`/credentials/resolve` must not be captured as credential id 'resolve'."""
    routes = [
        (i, m, r.path)
        for i, r in enumerate(_all_routes())
        if r.path.startswith("/api/v1/credentials/")
        for m in r.methods or ()
    ]
    literals = [(i, m, p) for i, m, p in routes if "{" not in p]
    by_id = [(i, m, p) for i, m, p in routes if p.startswith("/api/v1/credentials/{credential_id}")]
    assert literals and by_id, "credential routes moved?"
    for i, m, p in literals:
        later = [q for j, n, q in by_id if n == m and j < i]
        assert not later, f"{m} {p} is registered after {later} and would be read as a credential id"
