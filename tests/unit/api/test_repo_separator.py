"""A repository's sub-resources follow ``/-/`` at any depth; the old paths answer only where they mean one thing (#257).

``DELETE /api/v1/repos/acme/products/qa-config`` used to delete the QA config
of ``acme/products``, and the GitLab subgroup project
``acme/products/qa-config`` could not be reached at all. With the separator,
``/repos/acme/products/qa-config/-/qa-config`` is that project's QA config and
``/repos/acme/products/qa-config/-/``, with nothing after it, the project itself.
The old path without the separator still answers until
``repo_old_paths.OLD_PATHS_REMOVED_IN``, says so in a ``Deprecation`` header,
and is refused (409) when it also names a registered repository another way
that takes the request's method.

The repositories and their QA configs are stored through the repository
layer, not the API, so on a tree without the separator each test still gets
as far as its own assertion.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.api.app import create_app
from embry0.config import Embry0Config

PARENT = "acme/products"
#: A GitLab subgroup's project named like a sub-resource of its parent.
CHILD = "acme/products/qa-config"
PARENT_CONFIG = "version: 2\nqa_required: never\nworkspace_provider:\n  type: npm-workspaces-turbo\n"
CHILD_CONFIG = "version: 2\nqa_required: never\nworkspace_provider:\n  type: pnpm-workspaces\n"
#: RFC 9745: deprecated since 2026-10-30 00:00 UTC, the day the separator arrived (``repo_old_paths.DEPRECATED_SINCE``).
DEPRECATION = "@1793318400"


async def _configured(client: Any, repo: str, text: str) -> None:
    """``repo`` registered with ``text`` as its QA config, stored as the API stores it, without going through it."""
    await client.app.state.qa_configs_repo.upsert(repo, text, updated_by="test")


async def _both_configured(client: Any) -> None:
    """``acme/products`` and ``acme/products/qa-config`` registered, each with its own QA config."""
    await _configured(client, PARENT, PARENT_CONFIG)
    await _configured(client, CHILD, CHILD_CONFIG)


async def _config(client: Any, repo: str) -> str | None:
    row = await client.app.state.qa_configs_repo.get(repo)
    return str(row["yaml_text"]) if row is not None else None


async def _registered(client: Any) -> list[str]:
    return [row["repo"] for row in (await client.get("/api/v1/repos")).json()]


@pytest.mark.asyncio
async def test_a_subgroup_project_named_like_a_sub_resource_is_reached_after_the_separator(api_client) -> None:
    """GET and DELETE reach ``acme/products/qa-config``'s QA config; ``acme/products``'s is untouched."""
    await _both_configured(api_client)

    got = await api_client.get(f"/api/v1/repos/{CHILD}/-/qa-config")
    assert got.status_code == 200 and got.text == CHILD_CONFIG, (
        f"GET /repos/{CHILD}/-/qa-config must answer with {CHILD}'s QA config: {got.status_code} {got.text[:200]}"
    )
    deleted = await api_client.delete(f"/api/v1/repos/{CHILD}/-/qa-config")
    assert deleted.status_code == 204, deleted.text
    assert await _config(api_client, CHILD) is None, f"DELETE /repos/{CHILD}/-/qa-config left {CHILD}'s QA config"
    assert await _config(api_client, PARENT) == PARENT_CONFIG, (
        f"DELETE /repos/{CHILD}/-/qa-config must leave {PARENT}'s QA config alone"
    )
    assert {PARENT, CHILD} <= set(await _registered(api_client)), "deleting a QA config unregisters no repository"
    assert "Deprecation" not in got.headers and "Deprecation" not in deleted.headers, "a path with /-/ is current"


@pytest.mark.asyncio
async def test_a_subgroup_project_named_like_a_sub_resource_is_read_and_deleted_at_its_own_address(api_client) -> None:
    """``/repos/acme/products/qa-config/-/`` is the repository, while its path without ``/-/`` still means two things."""
    await _both_configured(api_client)

    got = await api_client.get(f"/api/v1/repos/{CHILD}/-/")
    body = got.json() if "json" in got.headers.get("content-type", "") else got.text
    assert got.status_code == 200 and isinstance(body, dict) and body.get("repo") == CHILD, (
        f"GET /repos/{CHILD}/-/ must answer with the repository {CHILD}: {got.status_code} {str(body)[:200]}"
    )
    assert body["has_qa_config"] is True and "Deprecation" not in got.headers, body
    deleted = await api_client.delete(f"/api/v1/repos/{CHILD}/-/")
    assert deleted.status_code == 204, f"DELETE /repos/{CHILD}/-/ must delete {CHILD}: {deleted.status_code}"
    registered = await _registered(api_client)
    assert CHILD not in registered and PARENT in registered, f"DELETE /repos/{CHILD}/-/ left {registered}"
    assert await _config(api_client, PARENT) == PARENT_CONFIG, f"DELETE /repos/{CHILD}/-/ touched {PARENT}'s QA config"


@pytest.mark.asyncio
@pytest.mark.parametrize("repo", ["acme/widgets", "acme/products/shop", "acme/history"])
async def test_every_repository_is_read_and_deleted_at_its_own_address(api_client, repo: str) -> None:
    """Depth 2 and 3, and a two-segment repository named like a sub-resource."""
    assert (await api_client.post("/api/v1/repos", json={"repo": repo, "forge": "gitlab"})).status_code == 201
    got = await api_client.get(f"/api/v1/repos/{repo}/-/")
    assert got.status_code == 200 and got.json().get("repo") == repo, (
        f"GET /repos/{repo}/-/ must answer with the repository: {got.status_code} {got.text[:200]}"
    )
    without_slash = await api_client.get(f"/api/v1/repos/{repo}/-")
    assert without_slash.status_code == 200 and without_slash.json().get("repo") == repo, (
        f"GET /repos/{repo}/- must answer as /-/ does, not redirect: {without_slash.status_code}"
    )
    assert (await api_client.delete(f"/api/v1/repos/{repo}/-/")).status_code == 204
    assert repo not in await _registered(api_client), f"DELETE /repos/{repo}/-/ left {repo} registered"
    assert (await api_client.get(f"/api/v1/repos/{repo}/-/")).status_code == 404


@pytest.mark.asyncio
async def test_the_repository_itself_is_its_path_without_the_separator(api_client) -> None:
    """A three-segment path that is no old path is the repository, for GET and DELETE alike."""
    repo = "acme/products/shop"
    assert (await api_client.post("/api/v1/repos", json={"repo": repo, "forge": "gitlab"})).status_code == 201
    detail = await api_client.get(f"/api/v1/repos/{repo}")
    assert detail.status_code == 200 and detail.json()["repo"] == repo, detail.text[:200]
    assert (await api_client.delete(f"/api/v1/repos/{repo}")).status_code == 204
    assert repo not in await _registered(api_client)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_an_old_path_that_also_names_a_registered_repository_is_refused(api_client, method: str) -> None:
    """``/repos/acme/products/qa-config`` is the QA config of ``acme/products`` and the registered repository."""
    await _both_configured(api_client)

    r = await api_client.request(method, f"/api/v1/repos/{CHILD}", content="version: 2\n")
    assert r.status_code == 409, (
        f"{method} /repos/{CHILD} names the QA config of {PARENT} and the registered repository {CHILD}; it must be "
        f"refused, not answered {r.status_code}: {r.text[:300]}"
    )
    readings = {
        (reading["repo"], reading["sub_resource"], reading["path"]) for reading in r.json()["detail"]["readings"]
    }
    assert readings == {
        (PARENT, "qa-config", f"/api/v1/repos/{PARENT}/-/qa-config"),
        (CHILD, None, f"/api/v1/repos/{CHILD}/-/"),
    }, readings
    assert r.headers.get("Deprecation") == DEPRECATION and "Link" not in r.headers, dict(r.headers)
    assert await _config(api_client, PARENT) == PARENT_CONFIG, f"{method} changed {PARENT}'s QA config"
    assert await _config(api_client, CHILD) == CHILD_CONFIG, f"{method} changed {CHILD}'s QA config"
    assert {PARENT, CHILD} <= set(await _registered(api_client)), f"{method} unregistered a repository"


@pytest.mark.asyncio
async def test_an_old_path_that_names_another_old_paths_registered_repository_is_refused(api_client) -> None:
    """``DELETE /repos/acme/web/environment/qa-config``: a variable of ``acme/web``, or ``acme/web/environment``'s config."""
    repo = "acme/web/environment"
    await _configured(api_client, repo, PARENT_CONFIG)

    r = await api_client.delete(f"/api/v1/repos/{repo}/qa-config")
    assert r.status_code == 409, f"the old path also names {repo}'s QA config; answered {r.status_code}: {r.text}"
    paths = {reading["path"] for reading in r.json()["detail"]["readings"]}
    assert paths == {"/api/v1/repos/acme/web/-/environment/qa-config", f"/api/v1/repos/{repo}/-/qa-config"}, paths
    assert await _config(api_client, repo) == PARENT_CONFIG


@pytest.mark.asyncio
@pytest.mark.parametrize("repo", ["acme/widgets", "acme/products/shop"])
async def test_an_old_path_that_names_one_thing_still_works_and_says_where_it_moved(api_client, repo: str) -> None:
    put = await api_client.put(f"/api/v1/repos/{repo}/qa-config", content=PARENT_CONFIG)
    got = await api_client.get(f"/api/v1/repos/{repo}/qa-config")
    deleted = await api_client.delete(f"/api/v1/repos/{repo}/qa-config")
    gone = await api_client.get(f"/api/v1/repos/{repo}/qa-config")

    assert put.status_code == 200 and put.json()["repo"] == repo, put.text
    assert got.status_code == 200 and got.text == PARENT_CONFIG, got.text
    assert deleted.status_code == 204 and gone.status_code == 404, (deleted.status_code, gone.status_code)
    successor = f'</api/v1/repos/{repo}/-/qa-config>; rel="successor-version"'
    for answer in (put, got, deleted, gone):
        method = answer.request.method
        assert answer.headers.get("Deprecation") == DEPRECATION, (
            f"{method} /repos/{repo}/qa-config answered {answer.status_code} without the Deprecation header"
        )
        assert answer.headers.get("Link") == successor, (method, answer.headers.get("Link"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", f"/api/v1/repos/{CHILD}", PARENT_CONFIG),
        ("POST", f"/api/v1/repos/{CHILD}/check", PARENT_CONFIG),
    ],
)
async def test_an_old_path_whose_other_reading_refuses_the_method_keeps_its_meaning(
    api_client, method: str, path: str, body: str
) -> None:
    """A repository is read and deleted, never PUT: ``PUT /repos/acme/products/qa-config`` can only be a QA config.

    The second case is ``POST .../qa-config/check`` of ``acme/products/qa-config``, which the repository
    ``acme/products/qa-config/check`` would not take either, were it registered.
    """
    await _both_configured(api_client)
    await _configured(api_client, f"{CHILD}/check", CHILD_CONFIG)
    r = await api_client.request(method, path, content=body)
    assert r.status_code == 200, (
        f"{method} {path} names one thing that takes {method}; it must keep its old meaning, not answer "
        f"{r.status_code}: {r.text[:300]}"
    )
    assert r.headers.get("Deprecation") == DEPRECATION, dict(r.headers)


@pytest.mark.asyncio
async def test_an_old_path_whose_repository_takes_no_put_still_sets_the_flag(api_client) -> None:
    """``PUT /repos/acme/products/active`` with ``acme/products/active`` registered: no repository is ever PUT."""
    for repo in (PARENT, f"{PARENT}/active"):
        assert (await api_client.post("/api/v1/repos", json={"repo": repo, "forge": "gitlab"})).status_code == 201
    r = await api_client.put(f"/api/v1/repos/{PARENT}/active", json={"active": False})
    assert r.status_code == 200 and r.json()["active"] is False, (
        f"PUT /repos/{PARENT}/active must set {PARENT}'s flag, its one reading that takes PUT: {r.status_code} "
        f"{r.text[:300]}"
    )


@pytest.mark.asyncio
async def test_an_old_paths_validation_error_says_where_it_moved(api_client) -> None:
    """A 422 is an answer of the old path too: it carries the headers, and the body FastAPI gives a 422."""
    r = await api_client.put("/api/v1/repos/acme/widgets/preferences", json={"no_such_field": True})
    assert r.status_code == 422, r.text
    assert r.headers.get("Deprecation") == DEPRECATION, f"a 422 of an old path has no Deprecation: {dict(r.headers)}"
    assert r.headers.get("Link") == '</api/v1/repos/acme/widgets/-/preferences>; rel="successor-version"', dict(
        r.headers
    )
    current = await api_client.put("/api/v1/repos/acme/widgets/-/preferences", json={"no_such_field": True})
    assert r.json() == current.json(), "the old path's 422 body differs from the one after the separator"


def test_an_old_paths_reading_does_not_depend_on_the_include_prefix() -> None:
    """Before FastAPI 0.137 an included route carries the ``/api/v1`` prefix in its path; from 0.137 it does not."""
    from embry0.api.v1 import repo_old_paths

    params = {"full_repo": "acme/web", "key": "API_URL"}
    for path in ("/repos/{full_repo:repo}/environment/{key}", "/api/v1/repos/{full_repo:repo}/environment/{key}"):
        reading = repo_old_paths._filled(SimpleNamespace(path=path), params)
        assert reading == repo_old_paths.Reading("acme/web", "environment/API_URL"), f"{path} reads {reading}"


def test_the_repository_routes_document_the_409_of_an_old_path() -> None:
    """``GET``/``DELETE /repos/{full_repo}`` answer 409 when the path is also an old path naming a registered one."""
    spec = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)).openapi()
    for method in ("get", "delete"):
        responses = spec["paths"]["/api/v1/repos/{full_repo}"][method]["responses"]
        assert "409" in responses, f"{method.upper()} /api/v1/repos/{{full_repo}} documents no 409: {sorted(responses)}"


@pytest.mark.asyncio
async def test_an_old_path_with_parameters_links_to_its_successor(api_client) -> None:
    put = await api_client.put(
        "/api/v1/repos/acme/widgets/environment",
        json={"variables": [{"key": "API_URL", "value": "http://localhost:3000", "var_type": "config"}]},
    )
    assert put.status_code == 200, put.text
    r = await api_client.delete("/api/v1/repos/acme/widgets/environment/API_URL")
    assert r.status_code == 204, r.text
    assert r.headers.get("Link") == '</api/v1/repos/acme/widgets/-/environment/API_URL>; rel="successor-version"'
    listed = (await api_client.get("/api/v1/repos/acme/widgets/-/environment")).json()["variables"]
    assert listed == [], listed
