"""Who may give an agent the coder role, and the record of it (#395), on the real app over a real database.

The role runs an agent on the developer node, with the job's branch and its pull request. It is written by the agent
routes' own guards, an admin key without a repository allowlist, and every change of it is audited with what the
agent held before and after.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_writes import MASTER, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

SCRIBE = {"type": "scribe", "description": "Writes the change", "model": "claude-sonnet-5", "tools": ["Read", "Edit"]}


async def _seeded(app: Any) -> Any:
    """The built-in agents as the orchestrator seeds them when it starts."""
    from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository
    from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents
    from embry0.workflows.qa.agent_seed import seed_qa_agent

    repo = AgentDefinitionsRepository(app.state.db)
    await seed_qa_agent(repo)
    await seed_missing_builtin_agents(repo)
    return app


async def _roles_audits(db: DatabasePool) -> list[dict[str, Any]]:
    rows = await db.fetch("SELECT details FROM audit_log WHERE action = 'agent.roles_changed' ORDER BY id")
    return [json.loads(r["details"]) if isinstance(r["details"], str) else dict(r["details"]) for r in rows]


async def test_the_role_is_written_by_an_unrestricted_admin_and_audited(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _ids):
        _row, reader = await ApiKeysRepository(app.state.db).create(
            name="reader", scope="read", user_id=None, created_by="tests"
        )
        made = await call(app, "POST", "/api/v1/agents", keys.open, {**SCRIBE, "roles": ["coder"]})
        assert made.status_code == 201, made.text
        assert made.json()["roles"] == ["coder"]
        assert (await call(app, "GET", "/api/v1/agents/scribe", MASTER)).json()["coding_agent"] is True

        for token, why in (
            (keys.limited, "a key with a repository allowlist"),
            (reader, "a key without the admin tier"),
        ):
            refused = await call(app, "PUT", "/api/v1/agents/scribe", token, {"roles": []})
            assert refused.status_code == 403, f"{why} changed an agent's roles: {refused.status_code} {refused.text}"
        assert (await call(app, "GET", "/api/v1/agents/scribe", MASTER)).json()["roles"] == ["coder"]

        taken = await call(app, "PUT", "/api/v1/agents/scribe", MASTER, {"roles": []})
        assert taken.status_code == 200 and taken.json()["roles"] == [], taken.text
        again = await call(app, "PUT", "/api/v1/agents/scribe", MASTER, {"roles": [], "description": "same roles"})
        assert again.status_code == 200, again.text

        assert await _roles_audits(app.state.db) == [
            {"agent_type": "scribe", "before": [], "after": ["coder"]},
            {"agent_type": "scribe", "before": ["coder"], "after": []},
        ], "each change is recorded once, with what the agent held before and after; a write that keeps them is not"


async def test_a_reset_that_changes_the_roles_is_audited(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _ids):
        await _seeded(app)
        assert (await call(app, "PUT", "/api/v1/agents/slopmaster", MASTER, {"roles": []})).status_code == 200
        reset = await call(app, "POST", "/api/v1/agents/slopmaster/reset", MASTER)
        assert reset.status_code == 200 and reset.json()["roles"] == ["coder"], reset.text
        audits = await _roles_audits(app.state.db)
        assert audits[-1] == {"agent_type": "slopmaster", "before": [], "after": ["coder"]}, audits


@pytest.mark.parametrize(
    ("agent_type", "roles", "says"),
    [
        ("developer", [], "'developer' always holds the coder role"),
        ("review", ["coder"], "'review' cannot hold the coder role: it is a step with a node of its own"),
        ("qa", ["coder"], "'qa' cannot hold the coder role"),
        ("proposal-brief", ["coder"], "'proposal-brief' cannot hold coder: no job runs it"),
        ("proposal-brief", ["docs"], "'proposal-brief' cannot hold docs: no job runs it"),
        ("triage", ["coder", "docs"], "'triage' cannot hold the coder role: it is a step with a node of its own"),
    ],
)
async def test_the_role_is_refused_where_it_cannot_run(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, agent_type: str, roles: list[str], says: str
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _ids):
        await _seeded(app)
        before = (await call(app, "GET", f"/api/v1/agents/{agent_type}", MASTER)).json()
        refused = await call(app, "PUT", f"/api/v1/agents/{agent_type}", MASTER, {"roles": roles})
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"].startswith("roles: ") and says in refused.json()["detail"], refused.json()
        after = (await call(app, "GET", f"/api/v1/agents/{agent_type}", MASTER)).json()
        assert after["roles"] == before["roles"] and await _roles_audits(app.state.db) == []


async def test_the_role_is_refused_when_a_template_would_have_two_coders(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = {
        "nodes": [
            {"node_id": "t", "agent_type": "triage"},
            {"node_id": "d", "agent_type": "developer"},
            {"node_id": "s", "agent_type": "scribe"},
        ],
        "edges": [
            {"edge_id": "e1", "source": "t", "target": "d", "edge_type": "flow"},
            {"edge_id": "e2", "source": "d", "target": "s", "edge_type": "flow"},
        ],
    }
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _ids):
        assert (await call(app, "POST", "/api/v1/agents", MASTER, SCRIBE)).status_code == 201
        saved = await call(
            app, "POST", "/api/v1/pipelines/templates", MASTER, {"name": "write-twice", "graph_definition": graph}
        )
        assert saved.status_code == 201, saved.text
        refused = await call(app, "PUT", "/api/v1/agents/scribe", MASTER, {"roles": ["coder"]})
        assert refused.status_code == 422, refused.text
        assert "'write-twice' has 2 steps whose agent has the coder role" in refused.json()["detail"], refused.json()


async def test_a_task_naming_no_template_follows_the_role(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _ids):
        refused = await call(app, "POST", "/api/v1/agents", MASTER, {**SCRIBE, "quest_template": ""})
        assert refused.status_code == 422 and refused.json()["detail"].startswith("quest_template: ''"), refused.text
        made = await call(app, "POST", "/api/v1/agents", MASTER, {**SCRIBE, "roles": ["coder"], "quest_template": ""})
        assert made.status_code == 201, made.text
        kept = await call(app, "PUT", "/api/v1/agents/scribe", MASTER, {"roles": []})
        assert kept.status_code == 422 and kept.json()["detail"].startswith("quest_template: ''"), (
            f"the role was taken from an agent whose task names no template: {kept.text}"
        )


async def test_a_coder_that_can_change_no_file_is_saved_with_a_warning(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _ids):
        reader = {**SCRIBE, "tools": ["Read", "Grep"], "roles": ["coder"]}
        made = await call(app, "POST", "/api/v1/agents", MASTER, reader)
        assert made.status_code == 201, made.text
        assert any("has the coder role but none of" in w for w in made.json()["warnings"]), made.json()["warnings"]
        shell = await call(app, "PUT", "/api/v1/agents/scribe", MASTER, {"tools": ["Read", "Bash"]})
        assert not any("coder role" in w for w in shell.json()["warnings"]), shell.json()["warnings"]


async def test_docs_is_granted_and_revoked_like_coder_on_any_agent_a_job_runs(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``docs`` lets a step that is no coder put files on the job's branch: the graph's own steps hold it as seeded,
    an operator takes it away or gives it with the same guards and record, and any number of steps may hold it."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _ids):
        await _seeded(app)
        assert (await call(app, "GET", "/api/v1/agents/triage", MASTER)).json()["roles"] == ["docs"]
        refused = await call(app, "PUT", "/api/v1/agents/triage", keys.limited, {"roles": []})
        assert refused.status_code == 403, refused.text
        taken = await call(app, "PUT", "/api/v1/agents/triage", MASTER, {"roles": []})
        assert taken.status_code == 200 and taken.json()["roles"] == [], taken.text
        given = await call(app, "PUT", "/api/v1/agents/triage", MASTER, {"roles": ["docs"]})
        assert given.status_code == 200 and given.json()["roles"] == ["docs"], given.text
        made = await call(app, "POST", "/api/v1/agents", MASTER, {**SCRIBE, "roles": ["docs"]})
        assert made.status_code == 201 and made.json()["roles"] == ["docs"], made.text
        assert await _roles_audits(app.state.db) == [
            {"agent_type": "triage", "before": ["docs"], "after": []},
            {"agent_type": "triage", "before": [], "after": ["docs"]},
            {"agent_type": "scribe", "before": [], "after": ["docs"]},
        ]


async def test_a_docs_agent_that_can_change_no_file_is_saved_with_a_warning(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _ids):
        made = await call(app, "POST", "/api/v1/agents", MASTER, {**SCRIBE, "tools": ["Read"], "roles": ["docs"]})
        assert made.status_code == 201, made.text
        assert any("has the docs role but none of" in w for w in made.json()["warnings"]), made.json()["warnings"]


async def test_a_change_of_roles_is_recorded_even_when_a_concurrent_write_came_between(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The roles before a write are the ones the write itself replaced, read under its lock, not the ones a read
    before it saw: a concurrent write between them cannot make a change look like none and go unrecorded."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _ids):
        assert (await call(app, "POST", "/api/v1/agents", MASTER, SCRIBE)).status_code == 201
        stale = {**(await app.state.agent_defs_repo.get("scribe")), "roles": []}
        # Another write gives scribe the coder role after this request read the row.
        await app.state.db.execute("UPDATE agent_definitions SET roles = ARRAY['coder'] WHERE type = 'scribe'")
        monkeypatch.setattr(app.state.agent_defs_repo, "get", AsyncMock(return_value=stale))
        taken = await call(app, "PUT", "/api/v1/agents/scribe", MASTER, {"roles": []})
        assert taken.status_code == 200, taken.text
        assert await _roles_audits(app.state.db) == [{"agent_type": "scribe", "before": ["coder"], "after": []}]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/agents", {**SCRIBE, "type": "scout", "roles": ["coder"]}),
        ("PUT", "/api/v1/agents/scribe", {"roles": ["coder"]}),
        ("POST", "/api/v1/agents/slopmaster/reset", None),
    ],
    ids=["create", "update", "reset"],
)
async def test_an_operate_key_writes_no_agent(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: Any
) -> None:
    """Writing an agent, its roles included, takes the admin tier: an operate key is refused, and nothing changes."""
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _ids):
        await _seeded(app)
        assert (await call(app, "POST", "/api/v1/agents", MASTER, SCRIBE)).status_code == 201
        assert (await call(app, "PUT", "/api/v1/agents/slopmaster", MASTER, {"roles": []})).status_code == 200
        _row, operate = await ApiKeysRepository(app.state.db).create(
            name="operator", scope="operate", user_id=None, created_by="tests"
        )
        refused = await call(app, method, path, operate, body)
        assert refused.status_code == 403, f"an operate key {method} {path}: {refused.status_code} {refused.text}"
        rows = {
            r["type"]: list(r["roles"]) for r in await app.state.db.fetch("SELECT type, roles FROM agent_definitions")
        }
        assert "scout" not in rows and rows["scribe"] == [] and rows["slopmaster"] == [], rows


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/agents", {**SCRIBE, "roles": ["coder"]}),
        ("PUT", "/api/v1/agents/scribe", {"roles": ["coder"]}),
        ("POST", "/api/v1/agents/slopmaster/reset", None),
    ],
    ids=["create", "update", "reset"],
)
def test_an_operator_session_writes_no_agent(method: str, path: str, body: Any) -> None:
    """A dashboard session below the admin tier is refused too, before any agent is read or written."""
    import os
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from embry0.api.app import create_app
    from embry0.api.session import SESSION_COOKIE, SessionData, encode_session, fold_tier
    from embry0.config import Embry0Config

    secret = "s" * 40
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(
            _env_file=None,
            auth_dev_mode=False,
            api_key="master-key",
            auth0_issuer="https://tenant.example.com/",
            auth0_client_id="client-id",
            auth0_client_secret="client-secret",
            session_secret=secret,
            sso_public_origin="https://embry0.example.com",
            sso_roles_claim="https://example.com/roles",
        )
    app = create_app(config)  # no database: an admin session gets past the tier, to an answer that is no 403
    for roles, refused in ((["embry0-operator"], True), (["embry0-admin"], False)):
        client = TestClient(app, raise_server_exceptions=False)
        data = SessionData(sub="auth0|u1", email="u@example.com", name="U", roles=roles, tier=fold_tier(roles))
        client.cookies.set(SESSION_COOKIE, encode_session(secret, data))
        answer = client.request(method, path, json=body, headers={"X-Requested-With": "XMLHttpRequest"})
        assert (answer.status_code == 403) is refused, f"{roles} {method} {path}: {answer.status_code} {answer.text}"


@pytest.mark.parametrize("body", [{"roles": ["docs"]}, {"description": "no such agent"}], ids=["roles", "other"])
async def test_an_update_of_an_agent_that_does_not_exist_is_a_404(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _ids):
        missing = await call(app, "PUT", "/api/v1/agents/nobody", MASTER, body)
        assert missing.status_code == 404, missing.text


async def test_a_reset_that_gives_a_template_two_coders_says_so(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A template saved while the Slopmaster held no coder role, next to the developer: a reset gives the role back,
    and the reset is done with a warning naming the template (plan_route skips it at plan time)."""
    graph = {
        "nodes": [
            {"node_id": "t", "agent_type": "triage"},
            {"node_id": "d", "agent_type": "developer"},
            {"node_id": "s", "agent_type": "slopmaster"},
        ],
        "edges": [
            {"edge_id": "e1", "source": "t", "target": "d", "edge_type": "flow"},
            {"edge_id": "e2", "source": "d", "target": "s", "edge_type": "flow"},
        ],
    }
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _ids):
        await _seeded(app)
        assert (await call(app, "PUT", "/api/v1/agents/slopmaster", MASTER, {"roles": []})).status_code == 200
        saved = await call(
            app, "POST", "/api/v1/pipelines/templates", MASTER, {"name": "both", "graph_definition": graph}
        )
        assert saved.status_code == 201, saved.text
        reset = await call(app, "POST", "/api/v1/agents/slopmaster/reset", MASTER)
        assert reset.status_code == 200 and reset.json()["roles"] == ["coder"], reset.text
        assert any("'both' has 2 steps whose agent has the coder role" in w for w in reset.json()["warnings"]), (
            reset.json()
        )
        again = await call(app, "POST", "/api/v1/agents/slopmaster/reset", MASTER)
        assert again.json()["warnings"] == [], "a reset that changes no role says nothing"
