"""Repo registry + DB-backed qa-config endpoint tests (RAV-1251)."""

from __future__ import annotations

import pytest

_V2_MIN = "version: 2\nqa_required: never\nworkspace_provider:\n  type: npm-workspaces-turbo\n"


@pytest.mark.asyncio
async def test_register_and_list(api_client) -> None:
    r = await api_client.post("/api/v1/repos", json={"repo": "acme/widgets", "notes": "pilot"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["repo"] == "acme/widgets"
    assert body["active"] is True

    rows = (await api_client.get("/api/v1/repos")).json()
    entry = next(row for row in rows if row["repo"] == "acme/widgets")
    assert entry["has_qa_config"] is False


@pytest.mark.asyncio
async def test_register_rejects_bad_shape(api_client) -> None:
    assert (await api_client.post("/api/v1/repos", json={"repo": "not-a-repo"})).status_code == 422


@pytest.mark.asyncio
async def test_put_qa_config_auto_registers_and_serves(api_client) -> None:
    r = await api_client.put("/api/v1/repos/acme/widgets/qa-config", content=_V2_MIN)
    assert r.status_code == 200, r.text

    got = await api_client.get("/api/v1/repos/acme/widgets/qa-config")
    assert got.status_code == 200
    assert got.text == _V2_MIN

    detail = (await api_client.get("/api/v1/repos/acme/widgets")).json()
    assert detail["has_qa_config"] is True
    assert detail["qa_config_updated_by"]


@pytest.mark.asyncio
async def test_put_qa_config_rejects_invalid_yaml(api_client) -> None:
    r = await api_client.put("/api/v1/repos/acme/widgets/qa-config", content="version: 1\n")
    assert r.status_code == 422
    assert r.json()["detail"].startswith("Input should be 2 (version)\n\nTechnical details:\n")
    assert "1 validation error for QAYamlConfigV2" in r.json()["detail"]


@pytest.mark.asyncio
async def test_put_qa_config_explains_a_typo_before_the_technical_details(api_client) -> None:
    text = _V2_MIN + "apps:\n  web:\n    boot_comand: npm run dev\n    frontend_url: http://localhost:3000\n"
    r = await api_client.put("/api/v1/repos/acme/widgets/qa-config", content=text)
    assert r.status_code == 422
    plain, technical = r.json()["detail"].split("\n\nTechnical details:\n")
    assert plain == "'boot_comand' is not a setting embry0 knows. Did you mean 'boot_command'? (app web)"
    assert "extra_forbidden" in technical


_V2_ON = (
    "version: 2\nworkspace_provider:\n  type: npm-workspaces-turbo\ndefaults:\n  sandbox_profile: {profile}\n"
    "apps:\n  web:\n    boot_command: npm run dev\n    frontend_url: http://localhost:5173\n"
    "    ready_checks:\n      - http: http://localhost:5173\n"
)


@pytest.mark.asyncio
async def test_check_refuses_a_machine_without_a_browser_and_stores_nothing(api_client, builtin_profile_seeded) -> None:
    await api_client.put("/api/v1/repos/acme/widgets/qa-config", content=_V2_MIN)
    r = await api_client.post("/api/v1/repos/acme/widgets/qa-config/check", content=_V2_ON.format(profile="slim"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["problems"][0]["plain"] == "The work machine 'slim' has no browser, so every test run would stop."
    assert (await api_client.get("/api/v1/repos/acme/widgets/qa-config")).text == _V2_MIN


@pytest.mark.asyncio
async def test_check_passes_a_browser_machine_on_a_subgroup_path(api_client, builtin_profile_seeded) -> None:
    r = await api_client.post(
        f"/api/v1/repos/{_GITLAB_REPO}/qa-config/check", content=_V2_ON.format(profile="qa-external")
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["summary"]["apps"][0]["machine"] == "qa-external"
    assert (await api_client.get(f"/api/v1/repos/{_GITLAB_REPO}/qa-config")).status_code == 404


@pytest.mark.asyncio
async def test_check_reads_values_from_the_repo_and_global_settings(api_client, builtin_profile_seeded) -> None:
    auth = "  auth:\n    storage_state_from:\n      secret: QA_LOGIN_STATE\n"
    text = _V2_ON.format(profile="qa-external").replace("apps:\n", auth + "apps:\n")
    url = "/api/v1/repos/acme/widgets/qa-config/check"
    missing = (await api_client.post(url, content=text)).json()
    assert [w["code"] for w in missing["warnings"]] == ["login_value_missing"]

    login = {"variables": [{"key": "QA_LOGIN_STATE", "value": "{}", "scope": "qa"}]}
    put = await api_client.put("/api/v1/repos/acme/widgets/environment", json=login)
    assert put.status_code == 200, put.text
    assert (await api_client.post(url, content=text)).json()["warnings"] == []

    await api_client.put("/api/v1/repos/acme/widgets/environment", json={"variables": []})
    put = await api_client.put("/api/v1/environment/global", json=login)
    assert put.status_code == 200, put.text
    assert (await api_client.post(url, content=text)).json()["warnings"] == []


@pytest.mark.asyncio
async def test_check_asks_for_docker_only_without_the_grant(api_client, builtin_profile_seeded) -> None:
    text = _V2_ON.format(profile="qa-jvm").replace("defaults:\n", "defaults:\n  mode: dind\n")
    text = text.replace("http://localhost:5173", "http://web:3000")
    await api_client.post("/api/v1/repos", json={"repo": "acme/widgets"})
    url = "/api/v1/repos/acme/widgets/qa-config/check"
    assert [w["code"] for w in (await api_client.post(url, content=text)).json()["warnings"]] == ["needs_docker"]

    await api_client.app.state.repos_repo.set_docker_access("acme/widgets", True)
    assert (await api_client.post(url, content=text)).json()["warnings"] == []


@pytest.mark.asyncio
async def test_get_qa_config_404_when_absent(api_client) -> None:
    assert (await api_client.get("/api/v1/repos/acme/ghost/qa-config")).status_code == 404


@pytest.mark.asyncio
async def test_delete_qa_config_keeps_registration(api_client) -> None:
    await api_client.put("/api/v1/repos/acme/widgets/qa-config", content=_V2_MIN)
    assert (await api_client.delete("/api/v1/repos/acme/widgets/qa-config")).status_code == 204
    assert (await api_client.get("/api/v1/repos/acme/widgets/qa-config")).status_code == 404
    detail = (await api_client.get("/api/v1/repos/acme/widgets")).json()
    assert detail["has_qa_config"] is False


@pytest.mark.asyncio
async def test_deactivate_hides_from_default_listing(api_client) -> None:
    await api_client.post("/api/v1/repos", json={"repo": "acme/widgets"})
    r = await api_client.put("/api/v1/repos/acme/widgets/active", json={"active": False})
    assert r.status_code == 200
    default = [row["repo"] for row in (await api_client.get("/api/v1/repos")).json()]
    assert "acme/widgets" not in default
    everything = [
        row["repo"] for row in (await api_client.get("/api/v1/repos", params={"include_inactive": "true"})).json()
    ]
    assert "acme/widgets" in everything


@pytest.mark.asyncio
async def test_delete_repo_cascades_config(api_client) -> None:
    await api_client.put("/api/v1/repos/acme/widgets/qa-config", content=_V2_MIN)
    assert (await api_client.delete("/api/v1/repos/acme/widgets")).status_code == 204
    assert (await api_client.get("/api/v1/repos/acme/widgets")).status_code == 404
    assert (await api_client.get("/api/v1/repos/acme/widgets/qa-config")).status_code == 404


@pytest.mark.asyncio
async def test_repo_detail_aggregates_env_and_prefs(api_client) -> None:
    await api_client.post("/api/v1/repos", json={"repo": "acme/widgets"})
    await api_client.put(
        "/api/v1/repos/acme/widgets/environment",
        json={
            "variables": [
                {"key": "API_URL", "value": "http://x", "var_type": "config"},
                {"key": "TOKEN", "value": "s3cret", "var_type": "secret"},
            ]
        },
    )
    detail = (await api_client.get("/api/v1/repos/acme/widgets")).json()
    assert detail["env_var_count"] == 2
    assert detail["env_secret_count"] == 1


@pytest.mark.asyncio
async def test_repo_detail_surfaces_the_stored_base_branch(api_client) -> None:
    """GET /repos/{repo} hands the preferences row through as a raw dict, which
    is what embry0.mcp_server's repo_get returns — so base_branch reaches MCP with no
    MCP-side change. Stored via the API, read back over HTTP."""
    await api_client.post("/api/v1/repos", json={"repo": "acme/widgets"})
    put = await api_client.put(
        "/api/v1/repos/acme/widgets/preferences",
        json={"base_branch": "release/2026.09"},
    )
    assert put.status_code == 200, put.text

    prefs = (await api_client.get("/api/v1/repos/acme/widgets/preferences")).json()
    assert prefs["base_branch"] == "release/2026.09"

    detail = (await api_client.get("/api/v1/repos/acme/widgets")).json()
    assert detail["preferences"]["base_branch"] == "release/2026.09"


# ---------------------------------------------------------------------------
# Forge discriminator + nested subgroup paths (#162)
# ---------------------------------------------------------------------------

_GITLAB_REPO = "acme/products/fbr"  # a real target: three segments


@pytest.mark.asyncio
async def test_defaults_to_github(api_client) -> None:
    """Every pre-#162 row meant GitHub; the default must preserve that."""
    r = await api_client.post("/api/v1/repos", json={"repo": "acme/widgets"})
    assert r.status_code == 201, r.text
    assert r.json()["forge"] == "github"


@pytest.mark.asyncio
async def test_registers_a_gitlab_subgroup_repo(api_client) -> None:
    """The old two-segment pattern rejected this outright."""
    r = await api_client.post("/api/v1/repos", json={"repo": _GITLAB_REPO, "forge": "gitlab", "notes": "firmware"})
    assert r.status_code == 201, r.text
    assert r.json() == {**r.json(), "repo": _GITLAB_REPO, "forge": "gitlab"}


@pytest.mark.asyncio
async def test_rejects_an_unknown_forge(api_client) -> None:
    r = await api_client.post("/api/v1/repos", json={"repo": "acme/widgets", "forge": "bitbucket"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_rejects_a_single_segment_repo(api_client) -> None:
    assert (await api_client.post("/api/v1/repos", json={"repo": "justaname"})).status_code == 422


@pytest.mark.asyncio
async def test_detail_and_lifecycle_work_for_a_subgroup_path(api_client) -> None:
    """Detail / active / delete must all address the full multi-segment path."""
    await api_client.post("/api/v1/repos", json={"repo": _GITLAB_REPO, "forge": "gitlab"})

    detail = await api_client.get(f"/api/v1/repos/{_GITLAB_REPO}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["repo"] == _GITLAB_REPO
    assert detail.json()["forge"] == "gitlab"

    deactivated = await api_client.put(f"/api/v1/repos/{_GITLAB_REPO}/active", json={"active": False})
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["active"] is False

    assert (await api_client.delete(f"/api/v1/repos/{_GITLAB_REPO}")).status_code == 204
    assert (await api_client.get(f"/api/v1/repos/{_GITLAB_REPO}")).status_code == 404


@pytest.mark.asyncio
async def test_sub_resources_reachable_on_a_subgroup_path(api_client) -> None:
    """The catch-all must not swallow /qa-config on a three-segment repo."""
    put = await api_client.put(f"/api/v1/repos/{_GITLAB_REPO}/qa-config", content=_V2_MIN)
    assert put.status_code in (200, 201), put.text

    got = await api_client.get(f"/api/v1/repos/{_GITLAB_REPO}/qa-config")
    assert got.status_code == 200, got.text
    assert "version: 2" in got.text

    detail = await api_client.get(f"/api/v1/repos/{_GITLAB_REPO}")
    assert detail.json()["has_qa_config"] is True


@pytest.mark.asyncio
async def test_register_without_forge_uses_the_configured_default(api_client) -> None:
    """The row must record the deployment's DEFAULT_FORGE, not the module
    constant — the two differ on exactly the tenants that care (Spec 1A §3.8)."""
    api_client.app.state.config.default_forge = "gitlab"
    r = await api_client.post("/api/v1/repos", json={"repo": "acme/group/backend"})
    assert r.status_code == 201, r.text
    assert r.json()["forge"] == "gitlab"


@pytest.mark.asyncio
async def test_register_without_forge_is_refused_when_the_deployment_has_no_default(api_client) -> None:
    """A guessed row is worse than no row: it makes forge_for succeed, so
    ERR_REPO_UNREGISTERED never fires and every sandbox clones github.com."""
    api_client.app.state.config.default_forge = ""
    r = await api_client.post("/api/v1/repos", json={"repo": "acme/group/backend"})
    assert r.status_code == 422, r.text
    assert "forge is required" in r.text
    assert (await api_client.get("/api/v1/repos/acme/group/backend")).status_code == 404


@pytest.mark.asyncio
async def test_explicit_forge_still_registers_without_a_default(api_client) -> None:
    api_client.app.state.config.default_forge = ""
    r = await api_client.post(
        "/api/v1/repos",
        json={"repo": "acme/group/backend", "forge": "gitlab"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["forge"] == "gitlab"


@pytest.mark.asyncio
async def test_qa_config_auto_registration_records_the_configured_forge(api_client) -> None:
    """The repos.forge column DEFAULT is 'github' — a correct backfill for
    pre-#162 rows, but as a live insert it is the same silent guess §3.8
    removed from repo_register."""
    api_client.app.state.config.default_forge = "gitlab"
    api_client.app.state.qa_configs_repo._default_forge = "gitlab"
    assert (await api_client.put("/api/v1/repos/acme/group/api/qa-config", content=_V2_MIN)).status_code == 200
    assert (await api_client.get("/api/v1/repos/acme/group/api")).json()["forge"] == "gitlab"


@pytest.mark.asyncio
async def test_qa_config_refuses_to_auto_register_when_there_is_no_default_forge(api_client) -> None:
    api_client.app.state.qa_configs_repo._default_forge = ""
    r = await api_client.put("/api/v1/repos/acme/group/api/qa-config", content=_V2_MIN)
    assert r.status_code == 422, r.text
    assert "is not registered" in r.json()["detail"]
    assert (await api_client.get("/api/v1/repos/acme/group/api")).status_code == 404


@pytest.mark.asyncio
async def test_qa_config_still_saves_for_an_already_registered_repo_without_a_default(api_client) -> None:
    api_client.app.state.config.default_forge = ""
    api_client.app.state.qa_configs_repo._default_forge = ""
    assert (
        await api_client.post("/api/v1/repos", json={"repo": "acme/group/api", "forge": "gitlab"})
    ).status_code == 201
    assert (await api_client.put("/api/v1/repos/acme/group/api/qa-config", content=_V2_MIN)).status_code == 200
    assert (await api_client.get("/api/v1/repos/acme/group/api")).json()["forge"] == "gitlab"


# --- #522: Docker access is an admin grant per repo -------------------------------------------


@pytest.mark.asyncio
async def test_a_new_repo_shows_no_docker_access(api_client) -> None:
    await api_client.post("/api/v1/repos", json={"repo": "acme/widgets"})
    rows = (await api_client.get("/api/v1/repos")).json()
    assert next(r for r in rows if r["repo"] == "acme/widgets")["docker_access"] is False
    assert (await api_client.get("/api/v1/repos/acme/widgets")).json()["docker_access"] is False


@pytest.mark.asyncio
async def test_put_docker_access_grants_revokes_and_audits(api_client) -> None:
    await api_client.post("/api/v1/repos", json={"repo": "acme/products/fbr", "forge": "gitlab"})
    # audit_log is not truncated between tests: read only what this test writes.
    before = await api_client.app.state.db.fetchval("SELECT COALESCE(MAX(id), 0) FROM audit_log")

    r = await api_client.put("/api/v1/repos/acme/products/fbr/docker-access", json={"enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["docker_access"] is True
    assert (await api_client.get("/api/v1/repos/acme/products/fbr")).json()["docker_access"] is True

    r = await api_client.put("/api/v1/repos/acme/products/fbr/docker-access", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["docker_access"] is False

    rows = await api_client.app.state.db.fetch(
        "SELECT action, details FROM audit_log WHERE id > $1 AND action LIKE 'repo_docker_access_%' ORDER BY id",
        before,
    )
    assert [r["action"] for r in rows] == ["repo_docker_access_granted", "repo_docker_access_revoked"]
    assert all(r["details"]["repo"] == "acme/products/fbr" for r in rows), rows


@pytest.mark.asyncio
async def test_put_docker_access_on_an_unregistered_repo_is_404(api_client) -> None:
    r = await api_client.put("/api/v1/repos/acme/ghost/docker-access", json={"enabled": True})
    assert r.status_code == 404, r.text
    assert (await api_client.get("/api/v1/repos/acme/ghost")).status_code == 404, "a grant must not register a repo"


@pytest.mark.asyncio
async def test_put_docker_access_rejects_anything_but_the_flag(api_client) -> None:
    await api_client.post("/api/v1/repos", json={"repo": "acme/widgets"})
    for body in ({}, {"enabled": True, "repo": "acme/other"}, {"enabled": "yes please"}):
        r = await api_client.put("/api/v1/repos/acme/widgets/docker-access", json=body)
        assert r.status_code == 422, (body, r.text)
    assert (await api_client.get("/api/v1/repos/acme/widgets")).json()["docker_access"] is False


def test_docker_access_writes_are_admin_tier() -> None:
    """The grant hands a repo the daemon that runs the credential proxies: an operator must not set it."""
    from embry0.api.app import create_app
    from embry0.api.deps import _admin_dependency
    from embry0.config import Embry0Config
    from tests.fixtures.routes import app_routes

    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    admin = set()
    for route in app_routes(app):
        if not route.path.startswith("/api/v1/repos") or route.dependant is None:
            continue
        calls = {d.call for d in route.dependant.dependencies}
        for method in route.methods or ():
            if _admin_dependency in calls:
                admin.add((method, route.path))
    assert ("PUT", "/api/v1/repos/{full_repo:repo}/docker-access") in admin, (
        f"PUT /repos/{{repo}}/docker-access must carry require_admin; admin routes under /repos: {sorted(admin)}"
    )


# The names a sub-resource route ends in that GET or DELETE /repos/{owner}/{name} answered for (#243).
_SHADOWED_ON_GET = (
    "areas",
    "credentials",
    "environment",
    "history",
    "preferences",
    "qa-config",
    "readiness",
    "slop-meter",
)
_SHADOWED_ON_DELETE = ("preferences", "qa-config")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _SHADOWED_ON_GET)
async def test_a_repository_named_like_a_sub_resource_is_read_at_its_address(api_client, name: str) -> None:
    """#243: ``GET /repos/acme/history`` answered with the history of a repository called ``acme``."""
    repo = f"acme/{name}"
    assert (await api_client.post("/api/v1/repos", json={"repo": repo})).status_code == 201
    r = await api_client.get(f"/api/v1/repos/{repo}")
    body = r.json() if "json" in r.headers.get("content-type", "") else r.text
    assert r.status_code == 200 and isinstance(body, dict) and body.get("repo") == repo, (
        f"GET /repos/{repo} must answer with the repository {repo}, got {r.status_code}: {str(body)[:200]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _SHADOWED_ON_DELETE)
async def test_a_repository_named_like_a_sub_resource_is_deleted_at_its_address(api_client, name: str) -> None:
    """#243: ``DELETE /repos/acme/qa-config`` deleted the QA config of a repository called ``acme``."""
    repo = f"acme/{name}"
    assert (await api_client.post("/api/v1/repos", json={"repo": repo})).status_code == 201
    r = await api_client.delete(f"/api/v1/repos/{repo}")
    listed = [row["repo"] for row in (await api_client.get("/api/v1/repos")).json()]
    assert r.status_code == 204 and repo not in listed, (
        f"DELETE /repos/{repo} must delete the repository {repo}; got {r.status_code} and it is still listed: "
        f"{repo in listed}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ("history", "preferences", "qa-config"))
async def test_a_repository_named_like_a_sub_resource_keeps_its_sub_resources(api_client, name: str) -> None:
    """#243: the repository ``acme/history`` still has a QA config, preferences and a history of its own."""
    repo = f"acme/{name}"
    put = await api_client.put(f"/api/v1/repos/{repo}/qa-config", content=_V2_MIN)
    assert put.status_code == 200 and put.json()["repo"] == repo, put.text
    assert (await api_client.get(f"/api/v1/repos/{repo}/qa-config")).text == _V2_MIN
    prefs = await api_client.put(f"/api/v1/repos/{repo}/preferences", json={"base_branch": "dev"})
    assert prefs.status_code == 200, prefs.text
    assert (await api_client.get(f"/api/v1/repos/{repo}/preferences")).json()["base_branch"] == "dev"
    history = (await api_client.get(f"/api/v1/repos/{repo}/history")).json()
    assert {e["surface"] for e in history["entries"]} == {"qa_config", "preferences"}, history
    detail = (await api_client.get(f"/api/v1/repos/{repo}")).json()
    assert isinstance(detail, dict) and detail.get("repo") == repo and detail.get("has_qa_config") is True, (
        f"GET /repos/{repo} must answer with the repository {repo} and its QA config: {str(detail)[:200]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "acme/widgets%0A",  # a newline ending a segment
        "acme%0Aevil/widgets",  # a newline inside one
        "acme/%2E",  # a `.` segment
        "acme/%2E%2E",  # a `..` segment
        "acme/%2E%2E/widgets",
    ],
)
async def test_a_path_post_repos_would_refuse_names_no_repository(api_client, path: str) -> None:
    """#243: a /repos route reads only a repository ``POST /repos`` would register, one segment rule for both."""
    r = await api_client.put(f"/api/v1/repos/{path}/qa-config", content=_V2_MIN)
    listed = [row["repo"] for row in (await api_client.get("/api/v1/repos")).json()]
    assert r.status_code == 404 and listed == [], (
        f"PUT /repos/{path}/qa-config must name no repository; got {r.status_code} and registered {listed!r}"
    )


@pytest.mark.asyncio
async def test_a_repository_whose_name_starts_with_a_dot_is_registered_and_reached(api_client) -> None:
    """An organization's ``.github`` repository: GitHub and GitLab allow a leading dot, only ``.``/``..`` not."""
    repo = "acme/.github"
    registered = await api_client.post("/api/v1/repos", json={"repo": repo})
    assert registered.status_code == 201, (
        f"POST /repos must register {repo}: {registered.status_code} {registered.text}"
    )
    detail = await api_client.get(f"/api/v1/repos/{repo}")
    assert detail.status_code == 200 and detail.json().get("repo") == repo, (
        f"GET /repos/{repo} must answer with the repository: {detail.status_code} {detail.text[:200]}"
    )
    put = await api_client.put(f"/api/v1/repos/{repo}/qa-config", content=_V2_MIN)
    assert put.status_code == 200 and put.json()["repo"] == repo, put.text
    assert (await api_client.get(f"/api/v1/repos/{repo}/qa-config")).text == _V2_MIN
    prefs = await api_client.put(f"/api/v1/repos/{repo}/preferences", json={"base_branch": "dev"})
    assert prefs.status_code == 200, prefs.text
    history = (await api_client.get(f"/api/v1/repos/{repo}/history")).json()
    assert {e["surface"] for e in history["entries"]} == {"qa_config", "preferences"}, history


@pytest.mark.asyncio
@pytest.mark.parametrize("repo", ["acme/.", "acme/..", "acme/../widgets"])
async def test_a_dot_or_dot_dot_segment_is_not_registered(api_client, repo: str) -> None:
    r = await api_client.post("/api/v1/repos", json={"repo": repo})
    listed = [row["repo"] for row in (await api_client.get("/api/v1/repos")).json()]
    assert r.status_code == 422 and listed == [], f"POST /repos must refuse {repo!r}: {r.status_code}, {listed!r}"
