"""Unit tests for embry0.mcp_server.client — httpx fully mocked via MockTransport."""

import json

import httpx
import pytest

from embry0.mcp_server.client import Embry0APIError, Embry0Client

BASE = "http://embry0.test"


def make_client(handler, api_key="test-key"):
    return Embry0Client(BASE, api_key, transport=httpx.MockTransport(handler))


class Seq(list):
    """Marks a sequence of successive responses for one endpoint."""


class Seen(list):
    """Request log. An int index is the request; a ``(method, path)`` key is its last JSON body."""

    def __getitem__(self, key):
        if isinstance(key, tuple):
            for request in reversed(self):
                if (request.method, request.url.path) == key:
                    return json.loads(request.content) if request.content else None
            raise KeyError(key)
        return super().__getitem__(key)


def capture(responses):
    """Handler that records requests and serves canned responses per (method, path)."""
    seen = Seen()
    headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = (request.method, request.url.path)
        headers[key] = request.headers
        if key not in responses:
            return httpx.Response(500, json={"detail": f"unexpected {key}"})
        entry = responses[key]
        payload = (entry.pop(0) if len(entry) > 1 else entry[0]) if isinstance(entry, Seq) else entry
        status = 200
        if isinstance(payload, tuple):
            status, payload = payload
        return httpx.Response(status, json=payload)

    handler.seen = seen
    handler.headers = headers
    return handler


async def test_headers_carry_auth_and_csrf():
    handler = capture({("GET", "/api/v1/health/ready"): {"status": "ok", "checks": {}}})
    client = make_client(handler)
    await client.health()
    request = handler.seen[0]
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["X-Requested-With"] == "XMLHttpRequest"


async def test_no_auth_header_without_key():
    handler = capture({("GET", "/api/v1/health/ready"): {"status": "ok"}})
    client = Embry0Client(BASE, "", transport=httpx.MockTransport(handler))
    await client.health()
    assert "Authorization" not in handler.seen[0].headers


async def test_base_url_from_env(monkeypatch):
    monkeypatch.setenv("EMBRY0_API_URL", "http://from-env:8200/")
    monkeypatch.setenv("EMBRY0_API_KEY", "env-key")
    client = Embry0Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert client.base_url == "http://from-env:8200"


async def test_error_surfaces_api_detail():
    handler = capture({("GET", "/api/v1/jobs/nope"): (404, {"detail": "Job not found"})})
    client = make_client(handler)
    with pytest.raises(Embry0APIError) as exc_info:
        await client.job_get("nope")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Job not found"
    assert "Job not found" in str(exc_info.value)


async def test_error_with_non_json_body():
    def handler(request):
        return httpx.Response(502, text="Bad Gateway")

    client = make_client(handler)
    with pytest.raises(Embry0APIError) as exc_info:
        await client.stats()
    assert exc_info.value.status_code == 502
    assert "Bad Gateway" in exc_info.value.detail


async def test_connection_error_wrapped():
    def handler(request):
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    with pytest.raises(Embry0APIError) as exc_info:
        await client.health()
    assert exc_info.value.status_code == 0
    assert "cannot reach" in exc_info.value.detail


async def test_issue_list_drops_none_params():
    handler = capture({("GET", "/api/v1/issues"): {"issues": [], "total": 0, "offset": 0, "limit": 50}})
    client = make_client(handler)
    await client.issue_list(status="open", limit=10)
    params = dict(handler.seen[0].url.params)
    assert params == {"status": "open", "limit": "10", "offset": "0"}


async def test_issue_create_body():
    handler = capture({("POST", "/api/v1/issues"): {"id": "iss-1", "status": "open"}})
    client = make_client(handler)
    result = await client.issue_create("Fix it", labels=["bug"], repo="acme/app", auto_triage=False)
    body = json.loads(handler.seen[0].content)
    assert body == {
        "title": "Fix it",
        "body": "",
        "labels": ["bug"],
        "priority": "medium",
        "repo": "acme/app",
        "auto_triage": False,
    }
    assert result["id"] == "iss-1"


async def test_job_create_research_recipe():
    handler = capture({("POST", "/api/v1/jobs"): {"job_id": "job-1", "status": "pending"}})
    client = make_client(handler)
    await client.job_create(
        task="Summarize RFC 2324",
        context={"type": "http", "url": "https://www.ietf.org/rfc/rfc2324.txt"},
    )
    body = json.loads(handler.seen[0].content)
    assert body == {
        "task": "Summarize RFC 2324",
        "context": {"type": "http", "url": "https://www.ietf.org/rfc/rfc2324.txt"},
    }


async def test_job_wait_returns_on_terminal_status():
    handler = capture(
        {
            ("GET", "/api/v1/jobs/j1"): Seq(
                [{"job_id": "j1", "status": "running"}, {"job_id": "j1", "status": "completed"}]
            )
        }
    )
    client = make_client(handler)
    job = await client.job_wait("j1", timeout=5.0, poll_interval=0.01)
    assert job["status"] == "completed"
    assert len(handler.seen) == 2


async def test_job_wait_timeout_flags_result():
    handler = capture({("GET", "/api/v1/jobs/j1"): {"job_id": "j1", "status": "running"}})
    client = make_client(handler)
    job = await client.job_wait("j1", timeout=0.05, poll_interval=0.01)
    assert job["status"] == "running"
    assert job["wait_timed_out"] is True


async def test_job_deliverables_adds_download_url_to_artifacts_only():
    rows = [
        {"id": "d1", "type": "artifact", "title": "bundle.zip"},
        {"id": "d2", "type": "report", "title": "report", "content": "text"},
    ]
    handler = capture({("GET", "/api/v1/jobs/j1/deliverables"): rows})
    client = make_client(handler)
    result = await client.job_deliverables("j1")
    assert result[0]["download_url"] == f"{BASE}/api/v1/jobs/j1/deliverables/d1/download"
    assert "download_url" not in result[1]


async def test_job_resume_and_cancel_paths():
    handler = capture(
        {
            ("POST", "/api/v1/jobs/j1/resume"): {"job_id": "j1", "status": "resuming", "choice": "abandon"},
            ("POST", "/api/v1/jobs/j1/cancel"): {"job_id": "j1", "status": "cancelled"},
        }
    )
    client = make_client(handler)
    resumed = await client.job_resume("j1", "abandon", guidance="stop it")
    assert resumed["status"] == "resuming"
    assert json.loads(handler.seen[0].content) == {"choice": "abandon", "guidance": "stop it"}
    cancelled = await client.job_cancel("j1")
    assert cancelled["status"] == "cancelled"


async def test_job_inputs_and_answer_paths():
    handler = capture(
        {
            ("GET", "/api/v1/jobs/j1/inputs"): [
                {"id": "inp-1", "job_id": "j1", "question": "Bump major?", "status": "pending"}
            ],
            ("POST", "/api/v1/jobs/j1/inputs/inp-1/answer"): {
                "id": "inp-1",
                "status": "answered",
                "answer": "yes",
            },
        }
    )
    client = make_client(handler)
    inputs = await client.job_inputs("j1")
    assert inputs == [{"id": "inp-1", "job_id": "j1", "question": "Bump major?", "status": "pending"}]
    answered = await client.job_input_answer("j1", "inp-1", "yes")
    assert answered["status"] == "answered"
    assert json.loads(handler.seen[1].content) == {"answer": "yes"}


async def test_template_validate_posts_graph():
    handler = capture({("POST", "/api/v1/pipelines/validate"): {"valid": False, "errors": ["no nodes"]}})
    client = make_client(handler)
    result = await client.template_validate({"name": "g", "nodes": []})
    assert result == {"valid": False, "errors": ["no nodes"]}
    assert json.loads(handler.seen[0].content) == {"name": "g", "nodes": []}


async def test_watcher_paths():
    handler = capture(
        {
            ("GET", "/api/v1/watcher/runs"): [{"run_id": "r1"}],
            ("POST", "/api/v1/watcher/run"): {"proposals": 0},
        }
    )
    client = make_client(handler)
    runs = await client.watcher_runs(limit=5)
    assert runs == [{"run_id": "r1"}]
    assert dict(handler.seen[0].url.params) == {"limit": "5"}
    assert await client.watcher_trigger() == {"proposals": 0}


async def test_watcher_calls_can_name_a_stack():
    handler = capture(
        {
            ("GET", "/api/v1/watcher/runs"): [],
            ("POST", "/api/v1/watcher/run"): {"action": "no_issue"},
        }
    )
    client = make_client(handler)
    await client.watcher_runs(limit=5, stack="acme/api")
    assert dict(handler.seen[0].url.params) == {"limit": "5", "stack": "acme/api"}
    await client.watcher_trigger(stack="acme/api")
    assert dict(handler.seen[1].url.params) == {"stack": "acme/api"}
    await client.watcher_trigger()
    assert dict(handler.seen[2].url.params) == {}, "no stack named: the route ticks every stack"


async def test_case_paths():
    handler = capture(
        {
            ("GET", "/api/v1/cases"): {"cases": [], "total": 0, "limit": 50, "next_cursor": None},
            ("GET", "/api/v1/cases/hc-1"): {"id": "hc-1", "stage": "merged"},
        }
    )
    client = make_client(handler)
    await client.case_list(repo="acme/widgets", open_only=True, limit=10)
    assert dict(handler.seen[0].url.params) == {
        "repo": "acme/widgets",
        "open_only": "true",
        "limit": "10",
    }
    # open_only=False is the API default, so it is not sent at all.
    await client.case_list(cursor="abc", fix_deployed=False, healed_verdict="healed")
    assert dict(handler.seen[1].url.params) == {
        "healed_verdict": "healed",
        "fix_deployed": "false",
        "limit": "50",
        "cursor": "abc",
    }
    await client.case_list(awaiting_decision=True)
    assert dict(handler.seen[2].url.params) == {"awaiting_decision": "true", "limit": "50"}
    assert await client.case_get("hc-1") == {"id": "hc-1", "stage": "merged"}


async def test_agent_update_sends_only_provided_fields():
    handler = capture({("PUT", "/api/v1/agents/developer"): {"type": "developer", "model": "m2"}})
    client = make_client(handler)
    await client.agent_update("developer", model="m2")
    assert json.loads(handler.seen[0].content) == {"model": "m2"}


async def test_agent_update_sends_identity_fields():
    handler = capture({("PUT", "/api/v1/agents/developer"): {"type": "developer"}})
    client = make_client(handler)
    await client.agent_update("developer", avatar="rogue", tagline="t")
    assert json.loads(handler.seen[0].content) == {"avatar": "rogue", "tagline": "t"}


async def test_agent_create_posts_the_definition_with_identity_defaults():
    handler = capture({("POST", "/api/v1/agents"): {"type": "lantern"}})
    client = make_client(handler)
    assert await client.agent_create("lantern", description="lights", model="m") == {"type": "lantern"}
    assert handler.seen[("POST", "/api/v1/agents")] == {
        "type": "lantern",
        "description": "lights",
        "model": "m",
        "system_prompt": "",
        "display_name": "",
        "avatar": "golem",
        "color": "slate",
        "tagline": "",
    }


async def test_agent_delete_calls_the_delete_route():
    handler = capture({("DELETE", "/api/v1/agents/lantern"): {"type": "lantern", "status": "deleted"}})
    client = make_client(handler)
    assert await client.agent_delete("lantern") == {"type": "lantern", "status": "deleted"}
    assert [(r.method, r.url.path) for r in handler.seen] == [("DELETE", "/api/v1/agents/lantern")]


async def test_agent_update_surfaces_the_provider_readiness_refusal():
    """The MCP tool saves through PUT /agents/{type}; its 422 reaches the caller verbatim."""
    detail = "provider 'xai' is not ready to run model 'grok-4.5': no credential is configured for it."
    handler = capture({("PUT", "/api/v1/agents/developer"): (422, {"detail": detail})})
    client = make_client(handler)
    with pytest.raises(Embry0APIError) as exc_info:
        await client.agent_update("developer", model="grok-4.5")
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == detail


_STORED = {
    "variables": [
        {"key": "PLAIN", "value": "clear", "var_type": "config", "description": "", "required": False, "scope": "app"},
        {
            "key": "SECRET_ONE",
            "value": "****",
            "var_type": "secret",
            "description": "",
            "required": True,
            "scope": "app",
        },
    ]
}
# The reveal route answers, so a client that still reveals fails on the
# assertion below rather than on a missing route.
_ROUTES = {
    ("GET", "/api/v1/environment/global"): _STORED,
    ("GET", "/api/v1/environment/global/SECRET_ONE/reveal"): {"key": "SECRET_ONE", "value": "real-secret"},
}


async def test_env_set_never_reveals_a_stored_secret():
    """A stored secret goes back as the mask, which the API keeps; nothing is revealed.

    Reveal is admin tier and audited, while writing the set is operator tier, so a
    write that revealed first failed for every operate key and wrote one
    secret_revealed audit row per secret for an admin.
    """
    handler = capture({**_ROUTES, ("PUT", "/api/v1/environment/global"): _STORED})
    client = make_client(handler)
    await client.env_set("NEW_KEY", "new-value", var_type="secret")
    reveals = [r.url.path for r in handler.seen if r.url.path.endswith("/reveal")]
    assert reveals == [], f"env_set revealed stored secrets before writing: {reveals}"
    body = handler.seen[("PUT", "/api/v1/environment/global")]
    by_key = {v["key"]: v for v in body["variables"]}
    assert by_key["SECRET_ONE"] == _STORED["variables"][1], "the stored secret did not go back as the mask"
    assert by_key["PLAIN"]["value"] == "clear"
    assert by_key["NEW_KEY"] == {
        "key": "NEW_KEY",
        "value": "new-value",
        "var_type": "secret",
        "description": "",
        "required": False,
        "scope": "app",
    }


async def test_env_delete_global_never_reveals_a_stored_secret():
    handler = capture({**_ROUTES, ("PUT", "/api/v1/environment/global"): _STORED})
    await make_client(handler).env_delete("PLAIN")
    reveals = [r.url.path for r in handler.seen if r.url.path.endswith("/reveal")]
    assert reveals == [], f"env_delete revealed stored secrets before writing: {reveals}"
    body = handler.seen[("PUT", "/api/v1/environment/global")]
    assert body["variables"] == [_STORED["variables"][1]]


async def test_an_operate_key_sets_a_variable_and_keeps_the_stored_secret():
    """Through the real environment router: an operate key writes, and the secret survives."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI

    from embry0.api.deps import bust_api_key_cache, require_auth
    from embry0.api.v1 import environment
    from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token

    token = "emb_" + "o" * 43
    operate = ApiKeyRow(
        id="key-operate00001",
        user_id=None,
        name="ops-bot",
        prefix="emb_oooooooo",
        scope="operate",
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
    )
    stored = {
        "rows": [
            {
                "key": "DB_PASSWORD",
                "value": "enc:hunter2-hunter2",
                "var_type": "secret",
                "description": "",
                "required": False,
                "scope": "app",
            },
        ]
    }

    async def get_global():
        return [dict(r) for r in stored["rows"]]

    async def set_global(rows):
        stored["rows"] = [dict(r) for r in rows]

    provider = MagicMock()
    provider.encrypt = AsyncMock(side_effect=lambda v: f"enc:{v}")
    provider.decrypt = AsyncMock(side_effect=lambda v: v.removeprefix("enc:"))
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: operate if h == hash_token(token) else None)
    keys.touch_last_used = AsyncMock()

    app = FastAPI()
    app.state.config = SimpleNamespace(auth_dev_mode=False, api_key="master-key", audit_log_path=None)
    app.state.api_keys_repo = keys
    app.state.users_repo = None
    app.state.db = None
    app.state.env_repo = SimpleNamespace(get_global=get_global, set_global=set_global)
    app.state.secrets_provider = provider
    app.include_router(environment.router, prefix="/api/v1", dependencies=[require_auth])

    bust_api_key_cache()
    client = Embry0Client(BASE, token, transport=httpx.ASGITransport(app=app))
    try:
        await client.env_set("LOG_LEVEL", "debug")
    except Embry0APIError as exc:
        pytest.fail(f"an operate key could not set a variable: {exc}")
    finally:
        bust_api_key_cache()
    by_key = {r["key"]: r for r in stored["rows"]}
    assert by_key["LOG_LEVEL"]["value"] == "debug"
    assert by_key["DB_PASSWORD"]["value"] == "enc:hunter2-hunter2", "the stored secret was overwritten"


async def test_env_set_replaces_existing_key():
    existing = {
        "variables": [
            {"key": "K", "value": "old", "var_type": "config", "description": "", "required": False, "scope": "app"}
        ]
    }
    handler = capture(
        {("GET", "/api/v1/environment/global"): existing, ("PUT", "/api/v1/environment/global"): {"variables": []}}
    )
    client = make_client(handler)
    await client.env_set("K", "new")
    body = json.loads(next(r for r in handler.seen if r.method == "PUT").content)
    assert [v["value"] for v in body["variables"] if v["key"] == "K"] == ["new"]


async def test_env_repo_scoped_paths_and_delete():
    handler = capture(
        {
            ("GET", "/api/v1/repos/acme/app/-/environment"): {"variables": []},
            ("DELETE", "/api/v1/repos/acme/app/-/environment/K"): (204, None),
        }
    )
    client = make_client(handler)
    await client.env_list(repo="acme/app")
    result = await client.env_delete("K", repo="acme/app")
    assert result == {"key": "K", "status": "deleted"}
    assert handler.seen[1].method == "DELETE"


async def test_env_delete_global_rebuilds_set_without_key():
    existing = {
        "variables": [
            {"key": "KEEP", "value": "v", "var_type": "config", "description": "", "required": False, "scope": "app"},
            {"key": "DROP", "value": "x", "var_type": "config", "description": "", "required": False, "scope": "app"},
        ]
    }
    handler = capture(
        {("GET", "/api/v1/environment/global"): existing, ("PUT", "/api/v1/environment/global"): {"variables": []}}
    )
    client = make_client(handler)
    await client.env_delete("DROP")
    body = json.loads(next(r for r in handler.seen if r.method == "PUT").content)
    assert [v["key"] for v in body["variables"]] == ["KEEP"]


async def test_env_delete_global_missing_key_raises_404():
    handler = capture({("GET", "/api/v1/environment/global"): {"variables": []}})
    client = make_client(handler)
    with pytest.raises(Embry0APIError) as exc_info:
        await client.env_delete("GHOST")
    assert exc_info.value.status_code == 404


async def test_invalid_repo_string_rejected():
    client = make_client(capture({}))
    with pytest.raises(Embry0APIError) as exc_info:
        await client.env_list(repo="not-a-repo")
    assert exc_info.value.status_code == 422


async def test_watcher_trigger_uses_long_read_timeout():
    seen_timeouts = []

    def handler(request):
        seen_timeouts.append(request.extensions.get("timeout"))
        return httpx.Response(200, json={"action": "no_issue"})

    client = make_client(handler)
    await client.watcher_trigger()
    assert seen_timeouts[0]["read"] == 900.0
    await client.watcher_trigger(timeout=5.0)
    assert seen_timeouts[1]["read"] == 5.0


async def test_logs_query_params_and_path():
    handler = capture({("GET", "/api/v1/logs/query"): {"query": "q", "count": 1, "lines": [{"line": "x"}]}})
    client = make_client(handler)
    result = await client.logs_query(container="acme-backend", search="(?i)error", since_minutes=30, limit=50)
    assert result["count"] == 1
    params = dict(handler.seen[0].url.params)
    assert params == {
        "container": "acme-backend",
        "search": "(?i)error",
        "since_minutes": "30",
        "limit": "50",
    }


# ── RAV-1251: repos / qa-config client methods ──────────────────────────────

_V2_MIN = "version: 2\nqa_required: never\nworkspace_provider:\n  type: npm-workspaces-turbo\n"


async def test_repo_list_and_get_paths():
    handler = capture(
        {
            ("GET", "/api/v1/repos"): [{"repo": "a/b", "active": True, "has_qa_config": False}],
            ("GET", "/api/v1/repos/a/b/-/"): {"repo": "a/b", "active": True, "has_qa_config": True},
        }
    )
    client = make_client(handler)
    rows = await client.repo_list()
    assert rows[0]["repo"] == "a/b"
    detail = await client.repo_get("a/b")
    assert detail["has_qa_config"] is True


async def test_repo_list_include_inactive_param():
    handler = capture({("GET", "/api/v1/repos"): []})
    client = make_client(handler)
    await client.repo_list(include_inactive=True)
    assert handler.seen[-1].url.params["include_inactive"] == "true"


async def test_repo_register_omits_the_forge_when_unspecified():
    """Spec 1A §3.8: the client must not guess "github" — the orchestrator
    applies its own DEFAULT_FORGE, or refuses when it has none."""
    handler = capture({("POST", "/api/v1/repos"): {"repo": "a/b", "active": True}})
    client = make_client(handler)
    out = await client.repo_register("a/b", notes="pilot")
    assert out["repo"] == "a/b"
    assert json.loads(handler.seen[-1].content) == {"repo": "a/b", "notes": "pilot"}


async def test_repo_register_forwards_the_forge():
    handler = capture({("POST", "/api/v1/repos"): {"repo": "g/s/p", "forge": "gitlab"}})
    client = make_client(handler)
    await client.repo_register("g/s/p", forge="gitlab")
    assert json.loads(handler.seen[-1].content)["forge"] == "gitlab"


async def test_repo_scoped_paths_carry_a_gitlab_subgroup_whole():
    """_split_repo used to shred `group/sub/project` into owner+name and 422."""
    nested = "acme/group/backend"
    handler = capture(
        {
            ("GET", f"/api/v1/repos/{nested}/-/"): {"repo": nested},
            ("GET", f"/api/v1/repos/{nested}/-/environment"): {"variables": []},
        }
    )
    client = make_client(handler)
    assert (await client.repo_get(nested))["repo"] == nested
    await client.env_list(repo=nested)
    assert handler.seen[-1].url.path == f"/api/v1/repos/{nested}/-/environment"


async def test_every_repo_scoped_call_names_its_sub_resource_after_the_separator():
    """#257: the repository ``acme/products/qa-config`` is read at ``/-/``; its path alone is also a QA config's."""
    repo = "acme/products/qa-config"
    base = f"/api/v1/repos/{repo}"
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"repo": repo, "variables": [], "bindings": []})

    client = make_client(handler)
    await client.repo_get(repo)
    await client.env_list(repo=repo)
    await client.env_set("API_URL", "http://localhost", repo=repo)
    await client.env_delete("API_URL", repo=repo)
    await client.qa_config_get(repo)
    await client.qa_config_set(repo, _V2_MIN)
    await client.repo_credentials_get(repo)
    await client.credential_bind(repo, "cred-000000000001")
    assert seen == [
        ("GET", f"{base}/-/"),
        ("GET", f"{base}/-/environment"),
        ("GET", f"{base}/-/environment"),
        ("PUT", f"{base}/-/environment"),
        ("DELETE", f"{base}/-/environment/API_URL"),
        ("GET", f"{base}/-/qa-config"),
        ("PUT", f"{base}/-/qa-config"),
        ("GET", f"{base}/-/credentials"),
        ("GET", f"{base}/-/credentials"),
        ("PUT", f"{base}/-/credentials"),
    ], f"a repo-scoped call left out the /-/ separator: {seen}"


async def test_qa_config_get_returns_raw_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/a/b/-/qa-config"
        return httpx.Response(200, text=_V2_MIN, headers={"Content-Type": "application/yaml"})

    client = make_client(handler)
    assert await client.qa_config_get("a/b") == _V2_MIN


async def test_qa_config_get_404_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "no QA config stored for this repo"})

    client = make_client(handler)
    with pytest.raises(Embry0APIError, match="no QA config stored"):
        await client.qa_config_get("a/b")


async def test_qa_config_set_puts_raw_yaml():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/api/v1/repos/a/b/-/qa-config"
        assert request.content.decode() == _V2_MIN
        assert request.headers["Content-Type"] == "application/yaml"
        return httpx.Response(200, json={"repo": "a/b", "updated_at": "now"})

    client = make_client(handler)
    out = await client.qa_config_set("a/b", _V2_MIN)
    assert out["repo"] == "a/b"


async def test_qa_config_set_validation_error_surfaces():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "qa.yaml v2 validation failed: boom"})

    client = make_client(handler)
    with pytest.raises(Embry0APIError, match="validation failed"):
        await client.qa_config_set("a/b", "version: 1\n")


async def test_repo_methods_reject_a_single_segment():
    client = make_client(capture({}))
    for coro in (client.repo_get("not-a-repo"), client.qa_config_get("not-a-repo")):
        with pytest.raises(Embry0APIError, match="group/subgroup/name"):
            await coro


async def test_a_repository_whose_name_starts_with_a_dot_goes_through():
    """An organization's ``.github`` repository is a repository: only ``.`` and ``..`` are refused."""
    handler = capture({("GET", "/api/v1/repos/acme/.github/-/"): {"repo": "acme/.github"}})
    client = make_client(handler)
    assert (await client.repo_get("acme/.github"))["repo"] == "acme/.github"
    assert handler.seen[-1].url.path == "/api/v1/repos/acme/.github/-/"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-repo",
        "a/b/",
        "/a/b",
        "a//b",
        "a/../b",
        "../a/b",
        "a/.",
        "a/..",
        "a/b\n",
    ],
)
async def test_repo_path_rejects_traversal_empty_segments_and_trailing_newline(bad):
    r"""`..` must never reach a URL, and `\Z` (not `$`) rejects a trailing newline."""
    client = make_client(capture({}))
    with pytest.raises(Embry0APIError, match="group/subgroup/name"):
        await client.repo_get(bad)


async def test_case_accept_path():
    handler = capture({("POST", "/api/v1/cases/hc-1/accept"): {"job_id": "job-1", "status": "dispatched"}})
    client = make_client(handler)
    assert (await client.case_accept("hc-1", accepted_by="Alex"))["job_id"] == "job-1"
    assert json.loads(handler.seen[0].content) == {"accepted_by": "Alex"}


async def test_org_act_path_and_body():
    handler = capture({("POST", "/api/v1/orgs/acme/actions"): {"owner": "acme", "action": "watch", "results": []}})
    client = make_client(handler)
    assert (await client.org_act("acme", "watch", ["acme/widgets"], dry_run=True))["action"] == "watch"
    assert json.loads(handler.seen[0].content) == {
        "action": "watch",
        "repos": ["acme/widgets"],
        "dry_run": True,
        "force": False,
    }


async def test_merge_approval_paths():
    handler = capture(
        {
            ("GET", "/api/v1/merge-approvals"): {"items": [], "mode": "dry_run"},
            ("GET", "/api/v1/merge-approvals/mrg-1"): {"id": "mrg-1"},
            ("POST", "/api/v1/merge-approvals/mrg-1/decide"): {"outcome": "would_merge"},
        }
    )
    client = make_client(handler)
    assert (await client.merge_approval_list(status="pending"))["mode"] == "dry_run"
    assert handler.seen[0].url.params["status"] == "pending"
    assert (await client.merge_approval_get("mrg-1"))["id"] == "mrg-1"
    decided = await client.merge_decide("mrg-1", decision="approve", decided_by="Alex", chat_id="19:c@thread.v2")
    assert decided["outcome"] == "would_merge"
    assert json.loads(handler.seen[2].content) == {
        "decision": "approve",
        "decided_by": "Alex",
        "chat_id": "19:c@thread.v2",
    }


async def test_qa_config_calls_forward_the_caller_bearer():
    """#443: qa_config bypassed _request, so the hosted endpoint sent the service key."""
    from embry0.mcp_server.client import caller_bearer

    handler = capture(
        {
            ("GET", "/api/v1/repos/acme/widgets/-/qa-config"): {},
            ("PUT", "/api/v1/repos/acme/widgets/-/qa-config"): {"repo": "acme/widgets"},
        }
    )
    client = make_client(handler)
    token = caller_bearer.set("caller-key")
    try:
        await client.qa_config_get("acme/widgets")
        await client.qa_config_set("acme/widgets", "version: 2\n")
    finally:
        caller_bearer.reset(token)
    assert handler.headers[("GET", "/api/v1/repos/acme/widgets/-/qa-config")]["authorization"] == "Bearer caller-key"
    assert handler.headers[("PUT", "/api/v1/repos/acme/widgets/-/qa-config")]["authorization"] == "Bearer caller-key"


async def test_credential_bind_replaces_same_role_and_keeps_others():
    handler = capture(
        {
            ("GET", "/api/v1/repos/acme/widgets/-/credentials"): {
                "repo": "acme/widgets",
                "bindings": [
                    {"credential_id": "cred-000000000001", "role": "forge", "pinned": False, "priority": 0},
                    {
                        "id": "bind-1",
                        "credential_id": "cred-000000000002",
                        "role": "llm",
                        "provider": "github",
                        "label": "ci-bot",
                        "pinned": True,
                        "priority": 0,
                    },
                ],
                "effective": {},
            },
            ("PUT", "/api/v1/repos/acme/widgets/-/credentials"): {
                "repo": "acme/widgets",
                "bindings": [],
                "effective": {},
            },
        }
    )
    client = make_client(handler)
    await client.credential_bind("acme/widgets", "cred-000000000009")
    put = handler.seen[("PUT", "/api/v1/repos/acme/widgets/-/credentials")]
    assert put["bindings"] == [
        {"credential_id": "cred-000000000002", "role": "llm", "pinned": True, "priority": 0},
        {"credential_id": "cred-000000000009", "role": "forge", "pinned": False, "priority": 0},
    ]


async def test_credential_calls_forward_the_caller_bearer():
    from embry0.mcp_server.client import caller_bearer

    handler = capture({("GET", "/api/v1/credentials"): [], ("GET", "/api/v1/repos/acme/widgets/-/qa-config"): {}})
    client = make_client(handler)
    token = caller_bearer.set("caller-key")
    try:
        await client.credential_list()
        await client.qa_config_get("acme/widgets")  # #443: must also carry the caller's bearer
    finally:
        caller_bearer.reset(token)
    assert handler.headers[("GET", "/api/v1/credentials")]["authorization"] == "Bearer caller-key"
    assert handler.headers[("GET", "/api/v1/repos/acme/widgets/-/qa-config")]["authorization"] == "Bearer caller-key"


async def test_job_logs_pages_to_the_tail():
    """#203: the API pages at 500 events; job_logs must follow the cursor to the end."""
    pages = Seq(
        [
            {"events": [{"n": 1}, {"n": 2}], "next_since_seq": 20, "has_more": True},
            {"events": [{"n": 3}], "next_since_seq": 30, "has_more": False},
        ]
    )
    handler = capture({("GET", "/api/v1/jobs/j1/logs/events"): pages})
    result = await make_client(handler).job_logs("j1")
    assert result == {"events": [{"n": 1}, {"n": 2}, {"n": 3}]}, f"did not page to the tail: {result}"
    cursors = [r.url.params.get("since_seq") for r in handler.seen]
    assert cursors == ["0", "20"]
