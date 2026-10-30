"""A repository's sub-resources at their old paths, without the ``/-/`` separator, until :data:`OLD_PATHS_REMOVED_IN`.

A repository's sub-resources follow a ``/-/`` separator (#257, as GitLab's
do): ``/repos/acme/products/-/qa-config`` is the QA config of
``acme/products``, and a path without ``/-/`` is the repository itself,
however many segments it has (``embry0/api/repo_route.py``). So is the
separator with nothing after it, ``/repos/acme/products/-/``, which the
dashboard, the CLI and the MCP server use because it never means two things.

Before, a sub-resource followed the repository directly, and a path of three
or more segments was read by registration order: ``DELETE
/repos/acme/products/qa-config`` deleted the QA config of ``acme/products``,
and the repository ``acme/products/qa-config``, a GitLab subgroup's project,
could not be reached. Those old paths still answer here, served by the same
handlers, until :data:`OLD_PATHS_REMOVED_IN`, wherever they mean one thing:

- **A path that can mean two things is refused.** Read without the
  separator, a path can name a repository as well as the sub-resource its
  old route serves: the whole path itself (``acme/products/qa-config``), or
  another old route's repository (``DELETE /repos/acme/web/environment/preferences``
  is the variable ``preferences`` of ``acme/web`` and the preferences of
  ``acme/web/environment``). Only readings whose route takes the request's
  method count: a repository is read and deleted, never PUT, so
  ``PUT /repos/acme/products/active`` sets the flag of ``acme/products``
  whatever is registered. When another reading names a registered
  repository, the request answers 409 with every reading and its path after
  the separator, and nothing runs. The registry is read by primary key, once
  for each other reading, after authentication. A path whose other readings
  name no registered repository keeps its old meaning.
- **Every answer says it is deprecated:** ``Deprecation`` (RFC 9745, the day
  the separator arrived) and, but on that 409, ``Link: <the path with the
  separator>; rel="successor-version"``, added as the answer is sent, so a
  404 or a 422 carries them too. The OpenAPI schema marks each old path
  ``deprecated``.

Only the routes of :data:`_OLD_PATHS` have an old path: a sub-resource added
later never had one.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from starlette.datastructures import MutableHeaders
from starlette.types import Message, Receive, Scope, Send

from embry0.api.repo_route import REPO_ADDRESS, SUB_RESOURCES
from embry0.api.route_table import route_template
from embry0.api.schemas.repo_path import REPO_PATH_PATTERN
from embry0.api.v1 import (
    areas,
    credentials,
    environment,
    qa_config,
    repo_history,
    repo_preferences,
    repo_setup,
    repos,
    slop_meter,
)

#: The release that removes the old paths: the one after the release that added the separator. Removing a route
#: needs a minor release before 1.0 (docs/releasing.md).
OLD_PATHS_REMOVED_IN = "0.2.0"

#: The day the separator arrived: what every old path's ``Deprecation`` header names (RFC 9745).
DEPRECATED_SINCE = datetime(2026, 10, 30, tzinfo=UTC)

#: Every sub-resource route a repository had before the separator, in the order the app matched them then.
_OLD_PATHS: tuple[tuple[str, str], ...] = (
    ("GET", f"{SUB_RESOURCES}areas"),
    ("POST", f"{SUB_RESOURCES}areas/map"),
    ("GET", f"{SUB_RESOURCES}slop-meter"),
    ("GET", f"{SUB_RESOURCES}environment"),
    ("GET", f"{SUB_RESOURCES}environment/resolve"),
    ("PUT", f"{SUB_RESOURCES}environment"),
    ("GET", f"{SUB_RESOURCES}environment/{{key}}/reveal"),
    ("DELETE", f"{SUB_RESOURCES}environment/{{key}}"),
    ("GET", f"{SUB_RESOURCES}environment/detect"),
    ("GET", f"{SUB_RESOURCES}credentials"),
    ("PUT", f"{SUB_RESOURCES}credentials"),
    ("GET", f"{SUB_RESOURCES}preferences"),
    ("PUT", f"{SUB_RESOURCES}preferences"),
    ("DELETE", f"{SUB_RESOURCES}preferences"),
    ("GET", f"{SUB_RESOURCES}qa-config"),
    ("PUT", f"{SUB_RESOURCES}qa-config"),
    ("DELETE", f"{SUB_RESOURCES}qa-config"),
    ("POST", f"{SUB_RESOURCES}qa-config/check"),
    ("GET", f"{SUB_RESOURCES}history"),
    ("POST", f"{SUB_RESOURCES}history/{{revision_id}}/undo"),
    ("GET", f"{SUB_RESOURCES}setup/cost"),
    ("POST", f"{SUB_RESOURCES}setup/propose"),
    ("GET", f"{SUB_RESOURCES}setup/proposal"),
    ("POST", f"{SUB_RESOURCES}setup/proposal/{{proposal_id}}/apply"),
    ("POST", f"{SUB_RESOURCES}setup/proposal/{{proposal_id}}/reject"),
    ("POST", f"{SUB_RESOURCES}setup/skip-testing"),
    ("GET", f"{SUB_RESOURCES}readiness"),
    ("PUT", f"{SUB_RESOURCES}active"),
    ("PUT", f"{SUB_RESOURCES}docker-access"),
)

#: The routers that serve :data:`_OLD_PATHS` with the separator.
_ROUTERS: tuple[APIRouter, ...] = (
    areas.router,
    slop_meter.router,
    environment.router,
    credentials.router,
    repo_preferences.router,
    qa_config.router,
    repo_history.router,
    repo_setup.router,
    repos.router,
)

_PARAM = re.compile(r"\{(\w+)(?::\w+)?\}")
#: A path parameter's convertor, which the OpenAPI schema leaves out: ``{full_repo:repo}`` is ``{full_repo}`` there.
_CONVERTOR = re.compile(r"\{(\w+):\w+\}")


def old_path(path: str) -> str:
    """A sub-resource route's path without the separator: ``/repos/{full_repo:repo}/qa-config``."""
    if SUB_RESOURCES not in path:
        raise ValueError(f"{path} is not a repository sub-resource's path")
    return path.replace(SUB_RESOURCES, f"{REPO_ADDRESS}/", 1)


def successor_path(path: str) -> str:
    """An old path's route path with the separator, include prefix or not: ``/repos/{full_repo:repo}/-/qa-config``."""
    if SUB_RESOURCES in path or f"{REPO_ADDRESS}/" not in path:
        raise ValueError(f"{path} is not an old path of a repository's sub-resource")
    return path.replace(f"{REPO_ADDRESS}/", SUB_RESOURCES, 1)


@dataclass(frozen=True, slots=True)
class Reading:
    """One thing a path can name: a repository's sub-resource, or the repository itself."""

    repo: str
    #: The sub-resource with its own segments filled in (``environment/API_URL``); empty for the repository itself.
    sub_resource: str = ""

    @property
    def path(self) -> str:
        """Its path under ``/api/v1`` that means it alone: after the separator, which ends the repository's address."""
        return f"/repos/{self.repo}/-/{self.sub_resource}"

    def describe(self, prefix: str) -> str:
        if not self.sub_resource:
            return f"the repository {self.repo} ({prefix}{self.path})"
        return f"the {self.sub_resource} of the repository {self.repo} ({prefix}{self.path})"


def _tail(path: str) -> str:
    """What follows the repository in a route path, include prefix or not: ``environment/{key}``.

    Before FastAPI 0.137 an included route's own ``path`` carries the include prefix (``/api/v1/repos/...``);
    from 0.137 it does not (``embry0/api/route_table.py``). Reading after :data:`REPO_ADDRESS` holds for both.
    """
    return path[path.index(REPO_ADDRESS) + len(REPO_ADDRESS) + 1 :]


def _filled(route: APIRoute, params: Mapping[str, Any]) -> Reading:
    """What ``route``, an old path, reads with these path parameters."""
    return Reading(str(params["full_repo"]), _PARAM.sub(lambda m: str(params[m.group(1)]), _tail(route.path)))


#: The methods a repository itself takes at its own address. A path is read as the repository only for these: a
#: ``PUT /repos/acme/products/active`` can only set the flag of ``acme/products``, whatever is registered.
_REPOSITORY_METHODS = frozenset(
    method
    for route in repos.router.routes
    if isinstance(route, APIRoute) and route.path == REPO_ADDRESS
    for method in route.methods or ()
)
if not _REPOSITORY_METHODS:
    raise RuntimeError(f"no {REPO_ADDRESS} route on the repos router: no old path could ever be refused")


def readings(method: str, rest: str) -> list[Reading]:
    """Every way the old path ``/repos/<rest>`` can be read for ``method``, each by a route that takes ``method``.

    Each old path that serves ``method`` and matches, in matching order, so the first is the one that serves it;
    then the repository ``rest`` itself, when it is a repository's path and the repository takes ``method``.
    """
    found = [
        _filled(route, match.groupdict())
        for route in deprecated_router.routes
        if isinstance(route, APIRoute)
        and method in (route.methods or ())
        and (match := route.path_regex.match(f"/repos/{rest}")) is not None
    ]
    if method in _REPOSITORY_METHODS and REPO_PATH_PATTERN.match(rest):
        found.append(Reading(rest))
    return found


def _prefix(request: Request) -> str:
    """What the app serves the route under, ``/api/v1``: its full template up to the repository."""
    template = route_template(request)
    return template[: template.index(REPO_ADDRESS)] if REPO_ADDRESS in template else ""


#: Set in the request's state when the old path is refused for naming more than one thing: that answer links to no
#: single successor.
_TWO_READINGS = "repo_old_path_two_readings"


async def refuse_two_readings(request: Request) -> None:
    """409 when the old path also names a registered repository another way; nothing has run yet."""
    served = _filled(request.scope["route"], request.path_params)
    rest = f"{served.repo}/{served.sub_resource}"
    others = [reading for reading in readings(request.method, rest) if reading != served]
    if not others:
        return
    repos_repo = request.app.state.repos_repo
    registered = [reading for reading in others if await repos_repo.get(reading.repo) is not None]
    if not registered:
        return
    prefix = _prefix(request)
    meant = [served, *registered]
    setattr(request.state, _TWO_READINGS, True)
    raise HTTPException(
        status_code=409,
        detail={
            "message": (
                f"{request.method} {prefix}/repos/{rest} can mean {len(meant)} things: "
                + "; ".join(reading.describe(prefix) for reading in meant)
                + ". A path without /-/ before a repository's sub-resource is deprecated and removed in "
                f"{OLD_PATHS_REMOVED_IN}; until then embry0 refuses one that names a registered repository "
                "in more than one way. Send the request to the path of the one you mean."
            ),
            "readings": [
                {"repo": reading.repo, "sub_resource": reading.sub_resource or None, "path": prefix + reading.path}
                for reading in meant
            ],
        },
    )


def _deprecation(request: Request, route: APIRoute, *, link: bool) -> dict[str, str]:
    headers = {"Deprecation": f"@{int(DEPRECATED_SINCE.timestamp())}"}
    if link:
        successor = _prefix(request) + _filled(route, request.path_params).path
        headers["Link"] = f'<{quote(successor, safe="/")}>; rel="successor-version"'
    return headers


class OldPathRoute(APIRoute):
    """A sub-resource's old path: every answer it sends says it is deprecated and where it moved.

    The headers go on the response as it is sent, so they reach every answer the route's own exception
    handling produces, a 409, a 404 or a 422 included, with the body the app gives it. A 405 never
    reaches it: the router answers a method no route at the path takes before any route runs.
    """

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_deprecated(message: Message) -> None:
            if message["type"] == "http.response.start":
                request = Request(scope)
                two_readings = bool(scope.get("state", {}).get(_TWO_READINGS))
                headers = MutableHeaders(scope=message)
                for name, value in _deprecation(request, self, link=not two_readings).items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await super().handle(scope, receive, send_deprecated)


def _old_path_aliases() -> APIRouter:
    """Each route of :data:`_OLD_PATHS` at its old path, served by the same handler, deprecated.

    Raises at import when one of them is not on :data:`_ROUTERS`, so an old path cannot quietly disappear.
    """
    served = {
        (method, route.path): route
        for router in _ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in sorted(route.methods or ())
    }
    missing = [f"{method} {path}" for method, path in _OLD_PATHS if (method, path) not in served]
    if missing:
        raise RuntimeError(f"repository sub-resource routes with an old path are not on their routers: {missing}")
    aliases = APIRouter(route_class=OldPathRoute)
    for method, path in _OLD_PATHS:
        route = served[(method, path)]
        successor = _CONVERTOR.sub(r"{\1}", path)
        aliases.add_api_route(
            old_path(path),
            route.endpoint,
            methods=[method],
            status_code=route.status_code,
            response_model=route.response_model,
            response_class=route.response_class,
            dependencies=[*route.dependencies, Depends(refuse_two_readings)],
            responses={409: {"description": "The path also names a registered repository another way."}},
            deprecated=True,
            name=f"{route.name}_old_path",
            summary=f"{route.summary or route.name.replace('_', ' ').capitalize()} (deprecated path)",
            description=(
                f"Deprecated: use `{method} /api/v1{successor}`, which this path calls. It is removed in "
                f"{OLD_PATHS_REMOVED_IN}. A path that also names a registered repository another way answers "
                f"409.\n\n{inspect.cleandoc(route.endpoint.__doc__ or '')}"
            ),
        )
    return aliases


#: The old paths: before the repository's detail route, which would read every one of them as a repository.
deprecated_router = _old_path_aliases()
