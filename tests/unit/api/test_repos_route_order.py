"""How a ``/repos`` path is read: the repository first, then its sub-resource after ``/-/`` (#162, #243, #257).

``/repos/{full_repo:repo}`` reads two or more segments, so GitLab nested
subgroups (``acme/products/fbr``) can be addressed at all (#162). A route
that read its first segments another way, such as ``{repo:path}``, shadowed
repositories: ``GET /repos/acme/history`` was the history of ``acme`` (#243).

A path of three or more segments was then read by registration order:
``DELETE /repos/acme/products/qa-config`` deleted the QA config of
``acme/products``, and the repository ``acme/products/qa-config`` could not
be reached (#257). A sub-resource now follows a ``/-/`` separator, which no
repository segment can be, so ``/repos/acme/products/qa-config/-/history`` is
that repository's history whatever the order. The old paths without the
separator are deprecated routes (``embry0/api/v1/repo_old_paths.py``) until
``OLD_PATHS_REMOVED_IN``: they must come before the repository's own routes,
and each refuses a path that also names a registered repository.

The guards against a route added the wrong way are shown firing on a
planted route, and every walk has a floor, so a quiet guard is not quiet
because it checks nothing.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from types import ModuleType

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from packaging.version import Version

from embry0.api.app import create_app
from embry0.api.route_table import ServedRoute, served_routes
from embry0.config import Embry0Config
from tests.fixtures.routes import app_routes

REPO_ROOT = Path(__file__).resolve().parents[3]

# Every /repos route that takes a parameter takes the repository first, read with the `repo` convertor (#243).
_REPO_PREFIX = "/api/v1/repos/{full_repo:repo}"
# What every sub-resource route starts with: the repository, then the separator (#257).
_SUB_RESOURCES = f"{_REPO_PREFIX}/-/"
# The first parameter of a /repos route, whatever it is called and whatever reads it: the repository it names.
_FIRST_PARAM = re.compile(r"^/api/v1/repos/(.*?)\{[^}]+\}")
_PARAM = re.compile(r"\{[^}]+\}")
#: Far below the sub-resource routes create_app() serves after the separator (29 in October 2026).
_MIN_SUB_RESOURCES = 25


def _app() -> FastAPI:
    return create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))


def _all_routes(app: FastAPI | None = None) -> list[ServedRoute]:
    """In matching order, which is what these tests are about."""
    return app_routes(app or _app())


def _parametrised(routes: list[ServedRoute]) -> list[ServedRoute]:
    """Every /repos route that takes a parameter, in matching order."""
    return [r for r in routes if r.path.startswith("/api/v1/repos/") and "{" in r.path]


def _detail(route: ServedRoute) -> bool:
    return _PARAM.sub("", route.path) == "/api/v1/repos/"


def _own_address(route: ServedRoute) -> bool:
    """The repository's own address after the separator, with nothing after it: ``/repos/{full_repo:repo}/-/``.

    ``/-`` without the final slash is the same route, not a redirect to it (#507: a redirect is labelled by its
    raw path in the metrics).
    """
    return route.path in (_SUB_RESOURCES, _SUB_RESOURCES.removesuffix("/"))


def _old(route: ServedRoute) -> bool:
    """An old path without the separator: a deprecated /repos route (#257), each one an ``OldPathRoute``."""
    return isinstance(route.route, APIRoute) and bool(route.route.deprecated)


def _old_path_module() -> ModuleType:
    """``embry0.api.v1.repo_old_paths``, imported where a test needs it: the others run on a tree without it."""
    from embry0.api.v1 import repo_old_paths

    return repo_old_paths


def _sub_resources(routes: list[ServedRoute]) -> list[ServedRoute]:
    """The current sub-resource routes; refuses a walk too short to be them all."""
    found = [r for r in _parametrised(routes) if not _detail(r) and not _own_address(r) and not _old(r)]
    assert len(found) >= _MIN_SUB_RESOURCES, (
        f"only {len(found)} /repos sub-resource routes found, fewer than {_MIN_SUB_RESOURCES}: the walk is missing "
        "routes, so a check over it would pass on nothing"
    )
    return found


def _split(route: ServedRoute) -> tuple[str, str]:
    """The route's path up to and after its first parameter: ``/repos/{full_repo:repo}`` and ``/-/history``."""
    match = _FIRST_PARAM.match(route.path)
    assert match is not None, f"{route.path} takes no parameter"
    return route.path[: match.end()], route.path[match.end() :]


def _served_by(routes: list[ServedRoute], method: str, path: str) -> ServedRoute | None:
    """The route that serves ``method path``: the first, in matching order, whose path and method both match."""
    return next((r for r in routes if r.methods and method in r.methods and r.path_regex.match(path)), None)


def _planted(path: str, *, deprecated: bool = False) -> FastAPI:
    """The real app with one more route, at the end of the matching order."""
    app = _app()
    router = APIRouter()
    router.add_api_route(path, lambda: None, methods=["GET"], deprecated=deprecated)
    app.include_router(router, prefix="/api/v1")
    return app


# --------------------------------------------------------------------- every route reads the repository first


def _not_reading_the_repository_first(routes: list[ServedRoute]) -> list[str]:
    return [r.path for r in _parametrised(routes) if not r.path.startswith(_REPO_PREFIX)]


def test_every_parametrised_repos_route_starts_with_the_repository():
    """#243: ``/repos/{repo:path}/-/badges`` would read ``acme`` as a repository, and the separator as part of it."""
    assert not _not_reading_the_repository_first(_all_routes()), f"these /repos routes do not start with {_REPO_PREFIX}"


def test_the_repository_first_check_fires_on_a_route_that_bypasses_the_convertor():
    planted = "/api/v1/repos/{repo:path}/-/badges"
    assert _not_reading_the_repository_first(served_routes(_planted("/repos/{repo:path}/-/badges").routes)) == [planted]


# --------------------------------------------------------------------- every sub-resource follows the separator


def _without_the_separator(routes: list[ServedRoute]) -> list[str]:
    """Sub-resource routes that are neither after ``/-/`` nor a deprecated old path."""
    return [r.path for r in _sub_resources(routes) if not r.path.startswith(_SUB_RESOURCES)]


def test_every_sub_resource_route_follows_the_separator():
    """#257: ``/repos/{full_repo:repo}/badges`` would read ``acme/products/badges`` as the badges of ``acme/products``."""
    assert not _without_the_separator(_all_routes()), (
        f"these /repos routes name a sub-resource without {_SUB_RESOURCES}"
    )


def test_the_separator_check_fires_on_a_sub_resource_without_it():
    planted = served_routes(_planted("/repos/{full_repo:repo}/badges").routes)
    assert _without_the_separator(planted) == ["/api/v1/repos/{full_repo:repo}/badges"]


def test_every_sub_resource_is_reached_after_the_separator_at_any_depth():
    """Depth 2 and 3, a repository named like a sub-resource and one whose name starts with a dot."""
    routes = _all_routes()
    repos = ("acme/widgets", "acme/products/shop", "acme/products/qa-config", "acme/history", "acme/.github")
    missed = []
    checked = 0
    for route in _sub_resources(routes):
        # The URL a client writes, separator and all, not one rebuilt from the route under test.
        tail = _PARAM.sub("1", _split(route)[1].removeprefix("/-/").lstrip("/"))
        for repo in repos:
            url = f"/api/v1/repos/{repo}/-/{tail}"
            for method in sorted(route.methods or ()):
                checked += 1
                served = _served_by(routes, method, url)
                if served is None or served.path != route.path:
                    missed.append(f"{method} {url} -> {served.path if served else None}, not {route.path}")
    assert checked >= _MIN_SUB_RESOURCES * len(repos), f"only {checked} sub-resource URLs were checked"
    assert not missed, f"sub-resource URLs do not reach their route: {missed}"


# --------------------------------------------------------------------- a path without the separator


def _repository_routes(routes: list[ServedRoute]) -> dict[str, str]:
    detail = {m: r.path for r in _parametrised(routes) if _detail(r) for m in r.methods or ()}
    assert {"GET", "DELETE"} <= set(detail), f"the repository detail routes moved: {detail}"
    return detail


def _names(routes: list[ServedRoute]) -> set[str]:
    names = {segment for r in _sub_resources(routes) for segment in _split(r)[1].split("/") if segment not in ("", "-")}
    names = {name for name in names if "{" not in name}
    assert {"history", "preferences", "qa-config"} <= names, f"the sub-resource names moved: {sorted(names)}"
    return names


def test_the_repository_is_reached_at_its_own_address_after_the_separator():
    """``/repos/acme/products/qa-config/-/`` is the repository at any depth, served as its path without ``/-/`` is."""
    routes = _all_routes()
    repos = ("acme/widgets", "acme/products/shop", "acme/products/qa-config", "acme/history", "acme/.github")
    detail = {m: r for r in _parametrised(routes) if _detail(r) for m in r.methods or ()}
    assert {"GET", "DELETE"} <= set(detail), f"the repository detail routes moved: {sorted(detail)}"
    missed = []
    for repo in repos:
        for method, bare in sorted(detail.items()):
            for url in (f"/api/v1/repos/{repo}/-/", f"/api/v1/repos/{repo}/-"):
                served = _served_by(routes, method, url)
                if served is None or served.route.endpoint is not bare.route.endpoint or _calls(served) != _calls(bare):
                    missed.append(f"{method} {url} -> {served.path if served else None}, not {bare.path}")
    assert not missed, f"the repository's own address after the separator does not reach it: {missed}"


def test_a_two_segment_path_is_always_the_repository():
    """#243: ``GET /repos/acme/history`` is the repository ``acme/history``, not the history of ``acme``."""
    routes = _all_routes()
    shadowed = []
    for name in sorted(_names(routes)):
        for method, path in sorted(_repository_routes(routes).items()):
            served = _served_by(routes, method, f"/api/v1/repos/acme/{name}")
            if served is None or served.path != path:
                shadowed.append(f"{method} /repos/acme/{name} -> {served.path if served else None}")
    assert not shadowed, f"a repository named like a sub-resource is served by another route: {shadowed}"


def test_a_deeper_path_without_the_separator_never_reaches_a_current_sub_resource_route():
    """``GET /repos/acme/products/history`` is the repository, or a deprecated old path that refuses two readings."""
    routes = _all_routes()
    wrong = []
    for name in sorted(_names(routes)):
        for method in ("GET", "PUT", "POST", "DELETE"):
            served = _served_by(routes, method, f"/api/v1/repos/acme/products/{name}")
            if served is not None and not (_detail(served) or _old(served)):
                wrong.append(f"{method} /repos/acme/products/{name} -> {served.path}")
    assert not wrong, f"a path without /-/ reached a sub-resource route that does not refuse two readings: {wrong}"


def test_a_repository_whose_name_starts_with_a_dot_has_an_address():
    """``acme/.github`` is a repository; ``acme/.`` and ``acme/..`` name none."""
    routes = _all_routes()
    for method, path in sorted(_repository_routes(routes).items()):
        served = _served_by(routes, method, "/api/v1/repos/acme/.github")
        assert served is not None and served.path == path, (
            f"{method} /repos/acme/.github must reach the repository, not {served.path if served else None}"
        )
        for bad in ("acme/.", "acme/.."):
            served = _served_by(routes, method, f"/api/v1/repos/{bad}")
            assert served is None, f"{method} /repos/{bad} must name no repository, but {served.path} serves it"


# --------------------------------------------------------------------- the old paths, until OLD_PATHS_REMOVED_IN


def _old_paths(routes: list[ServedRoute]) -> list[ServedRoute]:
    return [r for r in _parametrised(routes) if _old(r)]


def _served_old_paths(routes: list[ServedRoute]) -> list[ServedRoute]:
    """The old paths, until their removal; refuses a walk too short to be them all, which a check would pass on."""
    old = _old_paths(routes)
    assert len(old) >= _MIN_SUB_RESOURCES, f"only {len(old)} old /repos paths found, fewer than {_MIN_SUB_RESOURCES}"
    return old


def _old_paths_after_the_repository(routes: list[ServedRoute]) -> list[str]:
    """Old paths matched after a repository route, which would read them as the repository."""
    first_detail = min(i for i, r in enumerate(routes) if r.path.startswith("/api/v1/repos/") and _detail(r))
    return [r.path for i, r in enumerate(routes) if _old(r) and i > first_detail]


def test_the_old_paths_come_before_the_repository_routes():
    """After them, an old client's ``DELETE /repos/acme/products/qa-config`` would delete that repository instead."""
    routes = _all_routes()
    _served_old_paths(routes)
    assert not _old_paths_after_the_repository(routes), (
        "move app.include_router(repo_old_paths.deprecated_router, ...) above repos.router in create_app"
    )


def test_the_order_check_fires_on_an_old_path_after_the_repository_routes():
    planted = served_routes(_planted("/repos/{full_repo:repo}/badges", deprecated=True).routes)
    assert _old_paths_after_the_repository(planted) == ["/api/v1/repos/{full_repo:repo}/badges"]


def _calls(route: ServedRoute) -> set[object]:
    pending = [route.dependant]
    calls: set[object] = set()
    while pending:
        dependant = pending.pop()
        if dependant is None:
            continue
        calls.add(dependant.call)
        pending.extend(dependant.dependencies)
    return calls


def test_every_old_path_refuses_a_path_that_names_two_things():
    """No old path, a write least of all, may act on one repository when the path also names a registered one."""
    module = _old_path_module()
    old = _served_old_paths(_all_routes())
    assert len(old) == len(module._OLD_PATHS), f"{len(old)} old paths served, {len(module._OLD_PATHS)} listed"
    unguarded = [f"{sorted(r.methods or ())} {r.path}" for r in old if module.refuse_two_readings not in _calls(r)]
    assert not unguarded, f"these old paths do not run refuse_two_readings: {unguarded}"


def test_every_old_path_runs_its_successors_handler():
    """An old path is the route after the separator at another address: same handler, method and dependencies."""
    routes = _all_routes()
    current = {(m, r.path): r for r in _sub_resources(routes) for m in r.methods or ()}
    wrong = []
    for old in _served_old_paths(routes):
        for method in sorted(old.methods or ()):
            successor = current.get((method, _old_path_module().successor_path(old.path)))
            if successor is None or successor.route.endpoint is not old.route.endpoint:
                wrong.append(f"{method} {old.path}")
            elif _calls(successor) - _calls(old):
                wrong.append(f"{method} {old.path} skips {_calls(successor) - _calls(old)}")
            elif not isinstance(old.route, _old_path_module().OldPathRoute):
                wrong.append(f"{method} {old.path} does not mark its answers deprecated (OldPathRoute)")
    assert not wrong, f"old paths that do not stand for their route after the separator: {wrong}"


def old_paths_overdue(version: str) -> list[str]:
    """The old paths still served at ``version``, when it is the release that removes them or later."""
    if Version(version) < Version(_old_path_module().OLD_PATHS_REMOVED_IN):
        return []
    return sorted(f"{method} {r.path}" for r in _old_paths(_all_routes()) for method in r.methods or ())


def test_the_old_paths_are_gone_by_the_release_that_removes_them():
    version = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["version"]
    removed_in = _old_path_module().OLD_PATHS_REMOVED_IN
    overdue = old_paths_overdue(version)
    assert overdue == [], (
        f"embry0 {version} is {removed_in} or later, and the old /repos paths without /-/ are still served: "
        f"{overdue}. Remove embry0/api/v1/repo_old_paths.py, its include_router in app.py, its tests and the "
        "docs/api.md note"
    )


def test_the_removal_check_fires_at_the_removal_release():
    """The check above is quiet before the removal release only: it is not quiet by construction."""
    module = _old_path_module()
    assert len(old_paths_overdue(module.OLD_PATHS_REMOVED_IN)) == len(module._OLD_PATHS)
    assert old_paths_overdue("0.1.9") == []


# --------------------------------------------------------------------- unrelated: the credential routes


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
