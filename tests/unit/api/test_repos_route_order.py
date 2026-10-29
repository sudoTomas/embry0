"""The `/repos/{full_repo:repo}` catch-all must not shadow sub-resources (#162), nor they it (#243).

`repos` exposes a catch-all so GitLab nested subgroups
(`acme/products/fbr`) can be addressed at all. A catch-all matches
`/repos/acme/widgets/qa-config` just as happily as `/repos/acme/widgets`, so
registration order is the only thing keeping the sub-resource routers
reachable — and that is invisible at the call site.

This caught a live regression during #162: `repos.router` was registered
before `qa_config.router`, and `GET /repos/{o}/{r}/qa-config` silently started
returning the repo detail document instead of the config.

The other way round (#243): with the `path` convertor a sub-resource route
also read one segment as a repository, so `GET /repos/acme/history` was the
history of `acme` and the repository `acme/history` could not be read. The
`repo` convertor takes two segments or more, so a two-segment path is always
a repository's own address.
"""

import re

from embry0.api.app import create_app
from embry0.api.route_table import ServedRoute
from embry0.config import Embry0Config
from tests.fixtures.routes import app_routes

# Every suffix owned by a router other than `repos`.
_SUB_RESOURCES = (
    "qa-config",
    "check",
    "preferences",
    "environment",
    "credentials",
    "history",
    "undo",
    "cost",
    "propose",
    "proposal",
    "apply",
    "reject",
    "skip-testing",
    "readiness",
)


def _all_routes() -> list[ServedRoute]:
    """In matching order, which is what these tests are about."""
    return app_routes(create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)))


def _routes() -> list[ServedRoute]:
    return [r for r in _all_routes() if r.path.startswith("/api/v1/repos")]


def test_catch_all_is_registered_after_every_sub_resource():
    paths = [r.path for r in _routes()]
    catch_all = next(i for i, p in enumerate(paths) if p == "/api/v1/repos/{full_repo:repo}")
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
                assert "{full_repo:repo}" in route.path, (
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


# Every /repos route that takes a parameter takes the repository first, read with the `repo` convertor (#243).
_REPO_PREFIX = "/api/v1/repos/{full_repo:repo}"
# The first parameter of a /repos route, whatever it is called and whatever reads it: the repository it names.
_FIRST_PARAM = re.compile(r"^/api/v1/repos/(.*?)\{[^}]+\}")
_PARAM = re.compile(r"\{[^}]+\}")
#: Far below the parametrised /repos routes create_app() registers (31 in October 2026).
_MIN_PARAMETRISED = 25


def _parametrised() -> list[ServedRoute]:
    """Every /repos route that takes a parameter, in matching order; refuses a walk too short to be them all."""
    routes = [r for r in _routes() if r.path.startswith("/api/v1/repos/") and "{" in r.path]
    assert len(routes) >= _MIN_PARAMETRISED, (
        f"only {len(routes)} parametrised /repos routes found, fewer than {_MIN_PARAMETRISED}: the walk is "
        "missing routes, so a check over it would pass on nothing"
    )
    return routes


def _detail(route: ServedRoute) -> bool:
    return _PARAM.sub("", route.path) == "/api/v1/repos/"


def _split(route: ServedRoute) -> tuple[str, str]:
    """The route's path up to and after its first parameter: ``/repos/{full_repo:repo}`` and ``/history``."""
    match = _FIRST_PARAM.match(route.path)
    assert match is not None, f"{route.path} takes no parameter"
    return route.path[: match.end()], route.path[match.end() :]


def _served_by(routes: list[ServedRoute], method: str, path: str) -> ServedRoute | None:
    """The route that serves ``method path``: the first, in matching order, whose path and method both match."""
    return next((r for r in routes if r.methods and method in r.methods and r.path_regex.match(path)), None)


def test_every_parametrised_repos_route_starts_with_the_repository():
    """#243: a /repos route that reads its first segments another way shadows the address of a repository.

    ``/repos/{repo:path}/badges`` would read ``acme`` as a repository and answer ``GET /repos/acme/badges``,
    which is the repository ``acme/badges``; ``/repos/{owner}/{name}/badges`` could not name a subgroup's.
    """
    offenders = [r.path for r in _parametrised() if not r.path.startswith(_REPO_PREFIX)]
    assert not offenders, f"these /repos routes do not start with {_REPO_PREFIX}: {offenders}"


def test_a_repository_named_like_a_sub_resource_is_reached_by_its_own_address():
    """#243: ``GET /repos/acme/history`` is the repository ``acme/history``, not the history of ``acme``."""
    routes = _all_routes()
    detail = {m: r.path for r in _parametrised() if _detail(r) for m in r.methods or ()}
    assert {"GET", "DELETE"} <= set(detail), f"the repository detail routes moved: {detail}"
    names = {segment for r in _parametrised() for segment in _split(r)[1].split("/") if segment and "{" not in segment}
    assert {"history", "preferences", "qa-config"} <= names, f"the sub-resource names moved: {sorted(names)}"
    shadowed = []
    for name in sorted(names):
        for method, path in sorted(detail.items()):
            served = _served_by(routes, method, f"/api/v1/repos/acme/{name}")
            if served is None or served.path != path:
                shadowed.append(f"{method} /repos/acme/{name} -> {served.path if served else None}")
    assert not shadowed, f"a repository named like a sub-resource is served by another route: {shadowed}"


def test_every_sub_resource_url_still_reaches_its_route():
    """A sub-resource of a repository, a GitLab subgroup's or a dot-named one, is still served by its route."""
    routes = _all_routes()
    missed = []
    checked = 0
    for route in _parametrised():
        if _detail(route):
            continue
        first, rest = _split(route)
        for repo in ("acme/widgets", "acme/products/shop", "acme/.github"):
            url = _PARAM.sub(repo, first) + _PARAM.sub("1", rest)
            for method in sorted(route.methods or ()):
                checked += 1
                served = _served_by(routes, method, url)
                if served is None or served.path != route.path:
                    missed.append(f"{method} {url} -> {served.path if served else None}, not {route.path}")
    assert checked > 40, f"only {checked} sub-resource URLs were checked: the /repos routes moved"
    assert not missed, f"sub-resource URLs no longer reach their route: {missed}"


def test_a_repository_whose_name_starts_with_a_dot_has_an_address():
    """``acme/.github`` is a repository; ``acme/.`` and ``acme/..`` name none."""
    routes = _all_routes()
    detail = {m: r.path for r in _parametrised() if _detail(r) for m in r.methods or ()}
    for method, path in sorted(detail.items()):
        served = _served_by(routes, method, "/api/v1/repos/acme/.github")
        assert served is not None and served.path == path, (
            f"{method} /repos/acme/.github must reach the repository, not {served.path if served else None}"
        )
        for bad in ("acme/.", "acme/.."):
            served = _served_by(routes, method, f"/api/v1/repos/{bad}")
            assert served is None, f"{method} /repos/{bad} must name no repository, but {served.path} serves it"
