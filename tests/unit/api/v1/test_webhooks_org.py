"""The GitHub receiver under an organization webhook (ADR 0012).

The gate is additive. A delivery for a repository embry0 has not registered,
under an owner that is a registered, active GitHub organization, is recorded
as the need ``not_registered``, or registers the repository first when the
organization says so, and then reaches the same handlers it reaches without
organizations. A failure inside the gate never fails the delivery.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import webhooks as webhooks_api
from embry0.config import Embry0Config
from embry0.services.organizations import Organization

SECRET = "test-secret"
CRED = "cred-0123456789ab"


class FakeReposRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def registered_as(self, repo: str) -> str | None:
        return next((r for r in self.rows if r.lower() == repo.lower()), None)

    async def register(self, repo: str, notes: str = "", forge: str = "") -> dict[str, Any]:
        self.rows.setdefault(repo, {"repo": repo, "notes": notes, "forge": forge, "active": True})
        return self.rows[repo]


class FakeOrgsRepo:
    def __init__(self) -> None:
        self.orgs: dict[str, Organization] = {}

    async def get(self, owner: str) -> Organization | None:
        return self.orgs.get(owner.lower())


class FakeFactsRepo:
    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []

    async def note_webhook_event(self, owner: str, repo: str, **kw: Any) -> None:
        self.notes.append({"owner": owner, "repo": repo, **kw})


class FakeCredentialsRepo:
    """The two calls the credentials API's repository binding path makes."""

    def __init__(self) -> None:
        self.bindings: dict[str, list[dict[str, Any]]] = {}
        self.replaced: list[dict[str, Any]] = []

    async def bindings_for_scope(self, scope_kind: str, scope_value: str) -> list[dict[str, Any]]:
        assert scope_kind == "repo"
        return list(self.bindings.get(scope_value, []))

    async def replace_bindings(
        self,
        scope_kind: str,
        scope_value: str,
        bindings: list[dict[str, Any]],
        *,
        created_by: str,
        repo_forge: str | None = None,
    ) -> list[dict[str, Any]]:
        self.replaced.append(
            {
                "scope_kind": scope_kind,
                "scope_value": scope_value,
                "bindings": bindings,
                "created_by": created_by,
                "repo_forge": repo_forge,
            }
        )
        self.bindings[scope_value] = list(bindings)
        return bindings


class Unavailable:
    """A repository whose every call fails, as when the database went away."""

    def __getattr__(self, name: str) -> Any:
        async def fail(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(f"{name}: database unavailable")

        return fail


@pytest.fixture
def app() -> Any:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_webhook_secret=SECRET)
    app = create_app(config)
    app.state.issues_repo = MagicMock()
    app.state.inputs_repo = MagicMock()
    app.state.issue_executor = MagicMock()
    app.state.github_sync = MagicMock()
    app.state.github_sync.handle_webhook_event = AsyncMock(return_value={"status": "accepted"})
    app.state.delivery_recorder = MagicMock()
    app.state.delivery_recorder.record = AsyncMock(return_value=SimpleNamespace(matched=True))
    app.state.repos_repo = FakeReposRepo()
    app.state.organizations_repo = FakeOrgsRepo()
    app.state.org_repos_repo = FakeFactsRepo()
    app.state.credentials_repo = FakeCredentialsRepo()
    app.state.credential_resolver = MagicMock()
    app.state.db = None
    return app


def _register_org(app: Any, owner: str = "acme", *, credential_id: str | None = None, **settings: Any) -> None:
    app.state.organizations_repo.orgs[owner] = Organization(
        owner=owner, forge="github", credential_id=credential_id, settings=dict(settings)
    )


def _issues_payload(repo: str = "acme/widgets") -> bytes:
    return json.dumps(
        {
            "action": "opened",
            "issue": {"number": 7, "title": "Add a widget", "body": "", "labels": [], "html_url": ""},
            "repository": {"full_name": repo},
            "sender": {"login": "octocat"},
        }
    ).encode()


def _pull_request_payload(repo: str = "acme/widgets") -> bytes:
    return json.dumps(
        {
            "action": "opened",
            "pull_request": {
                "number": 3,
                "html_url": f"https://github.com/{repo}/pull/3",
                "state": "open",
                "head": {"ref": "embry0/fix-widget", "sha": "abc123", "repo": {"full_name": repo}},
                "base": {"repo": {"full_name": repo}},
            },
            "repository": {"full_name": repo},
            "sender": {"login": "octocat"},
        }
    ).encode()


_PAYLOADS: dict[str, Callable[[], bytes]] = {"issues": _issues_payload, "pull_request": _pull_request_payload}


def _handler_mock(app: Any, event: str) -> AsyncMock:
    """The call each handler makes into the rest of embry0."""
    mock = app.state.github_sync.handle_webhook_event if event == "issues" else app.state.delivery_recorder.record
    assert isinstance(mock, AsyncMock)
    return mock


async def _deliver(app: Any, body: bytes, *, event: str = "issues", delivery: str = "d-1") -> Any:
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/v1/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": event,
                "X-GitHub-Delivery": delivery,
                "Content-Type": "application/json",
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["issues", "pull_request"])
async def test_an_unregistered_repo_under_a_registered_owner_reaches_the_same_handler_calls(
    app: Any, event: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_org(app)
    body = _PAYLOADS[event]()
    handler = _handler_mock(app, event)

    gated = await _deliver(app, body, event=event)
    assert app.state.org_repos_repo.notes == [
        {
            "owner": "acme",
            "repo": "acme/widgets",
            "event": event,
            "action": "opened",
            "delivery_id": "d-1",
            "registered": False,
        }
    ]
    assert app.state.repos_repo.rows == {}

    # The same delivery with no gate at all: the receiver as it was before organizations.
    async def no_gate(*args: Any) -> None:
        return None

    monkeypatch.setattr(webhooks_api, "_organization_gate", no_gate)
    plain = await _deliver(app, body, event=event)

    assert gated.status_code == plain.status_code == 200
    assert gated.json() == plain.json()
    assert handler.await_count == 2
    assert handler.await_args_list[0] == handler.await_args_list[1]


@pytest.mark.asyncio
async def test_an_unhandled_event_under_a_registered_owner_is_recorded_and_ignored_as_before(app: Any) -> None:
    _register_org(app)
    body = json.dumps({"ref": "refs/heads/main", "repository": {"full_name": "acme/widgets"}}).encode()
    resp = await _deliver(app, body, event="push")
    assert resp.json() == {"status": "ignored", "event": "push", "action": ""}
    assert app.state.org_repos_repo.notes[0]["event"] == "push"


@pytest.mark.asyncio
async def test_auto_register_registers_the_repo_and_the_delivery_proceeds(app: Any) -> None:
    _register_org(app, auto_register=True)
    resp = await _deliver(app, _issues_payload())
    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted", "auto_registered": "acme/widgets"}
    app.state.github_sync.handle_webhook_event.assert_awaited_once()
    row = app.state.repos_repo.rows["acme/widgets"]
    assert row["forge"] == "github"
    assert "organization acme" in row["notes"]
    assert app.state.org_repos_repo.notes[0]["registered"] is True
    assert app.state.credentials_repo.replaced == []  # the organization has no credential of its own


@pytest.mark.asyncio
async def test_auto_register_binds_the_organizations_credential_like_the_credentials_api(app: Any) -> None:
    _register_org(app, credential_id=CRED, auto_register=True)
    resp = await _deliver(app, _issues_payload("acme/Widgets"))
    assert resp.json()["auto_registered"] == "acme/Widgets"
    assert app.state.credentials_repo.replaced == [
        {
            "scope_kind": "repo",
            "scope_value": "acme/widgets",
            "bindings": [{"credential_id": CRED, "role": "forge", "pinned": False, "priority": 0}],
            "created_by": "webhook",
            "repo_forge": "github",
        }
    ]
    app.state.credential_resolver.invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_auto_register_keeps_the_repos_other_bindings_and_a_forge_binding_someone_chose(app: Any) -> None:
    llm = {"credential_id": "cred-aaaaaaaaaaaa", "role": "llm", "pinned": True, "priority": 1}
    app.state.credentials_repo.bindings["acme/widgets"] = [{**llm, "label": "llm", "status": "active"}]
    _register_org(app, credential_id=CRED, auto_register=True)
    await _deliver(app, _issues_payload())
    assert app.state.credentials_repo.replaced[0]["bindings"] == [
        llm,
        {"credential_id": CRED, "role": "forge", "pinned": False, "priority": 0},
    ]

    chosen = {"credential_id": "cred-bbbbbbbbbbbb", "role": "forge", "pinned": False, "priority": 0}
    app.state.credentials_repo.bindings["acme/gadgets"] = [chosen]
    app.state.credentials_repo.replaced.clear()
    await _deliver(app, _issues_payload("acme/gadgets"), delivery="d-2")
    assert "acme/gadgets" in app.state.repos_repo.rows
    assert app.state.credentials_repo.replaced == []


@pytest.mark.asyncio
async def test_second_delivery_after_auto_register_is_an_ordinary_delivery(app: Any) -> None:
    _register_org(app, auto_register=True)
    await _deliver(app, _issues_payload())
    resp = await _deliver(app, _issues_payload(), delivery="d-2")
    assert resp.json() == {"status": "accepted"}
    assert len(app.state.org_repos_repo.notes) == 1
    assert app.state.github_sync.handle_webhook_event.await_count == 2


@pytest.mark.asyncio
async def test_auto_register_must_be_the_boolean_true(app: Any) -> None:
    _register_org(app, auto_register="yes")
    resp = await _deliver(app, _issues_payload())
    assert resp.json() == {"status": "accepted"}
    assert app.state.repos_repo.rows == {}
    assert app.state.org_repos_repo.notes[0]["registered"] is False


@pytest.mark.asyncio
async def test_a_gitlab_organization_named_like_the_github_owner_is_not_this_owner(app: Any) -> None:
    app.state.organizations_repo.orgs["acme"] = Organization(
        owner="acme", forge="gitlab", credential_id=CRED, settings={"auto_register": True}
    )
    resp = await _deliver(app, _issues_payload())
    assert resp.json() == {"status": "accepted"}
    app.state.github_sync.handle_webhook_event.assert_awaited_once()
    assert app.state.org_repos_repo.notes == []
    assert app.state.repos_repo.rows == {}
    assert app.state.credentials_repo.replaced == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["organizations_repo", "org_repos_repo", "credentials_repo"])
async def test_a_failing_repository_in_the_gate_never_fails_the_delivery(app: Any, failing: str) -> None:
    _register_org(app, credential_id=CRED, auto_register=True)
    setattr(app.state, failing, Unavailable())
    resp = await _deliver(app, _issues_payload())
    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted"}
    app.state.github_sync.handle_webhook_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failing_needs_sync_never_fails_the_delivery_or_hides_the_registration(app: Any) -> None:
    """The gate syncs the needs after it registers (ADR 0011); a sync that
    fails is logged and left to the next discovery run."""
    _register_org(app, auto_register=True)
    app.state.org_needs_repo = Unavailable()
    resp = await _deliver(app, _issues_payload())
    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted", "auto_registered": "acme/widgets"}
    app.state.github_sync.handle_webhook_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_without_an_organization_keeps_the_old_path(app: Any) -> None:
    resp = await _deliver(app, _issues_payload("stranger/widgets"))
    assert resp.json() == {"status": "accepted"}
    app.state.github_sync.handle_webhook_event.assert_awaited_once()
    assert app.state.org_repos_repo.notes == []
    assert app.state.repos_repo.rows == {}


@pytest.mark.asyncio
async def test_paused_organization_keeps_the_old_path(app: Any) -> None:
    _register_org(app)
    app.state.organizations_repo.orgs["acme"].status = "paused"
    resp = await _deliver(app, _issues_payload())
    assert resp.json() == {"status": "accepted"}
    assert app.state.org_repos_repo.notes == []


@pytest.mark.asyncio
async def test_registered_repo_under_a_registered_owner_keeps_the_old_path(app: Any) -> None:
    _register_org(app)
    await app.state.repos_repo.register("Acme/Widgets", forge="github")
    resp = await _deliver(app, _issues_payload("acme/widgets"))
    assert resp.json() == {"status": "accepted"}
    assert app.state.org_repos_repo.notes == []
    assert list(app.state.repos_repo.rows) == ["Acme/Widgets"]


@pytest.mark.asyncio
async def test_a_deployment_without_organization_storage_keeps_the_old_path(app: Any) -> None:
    del app.state.organizations_repo
    resp = await _deliver(app, _issues_payload())
    assert resp.json() == {"status": "accepted"}
    app.state.github_sync.handle_webhook_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_bad_signature_is_refused_before_the_gate(app: Any) -> None:
    _register_org(app)
    body = _issues_payload()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=body,
            headers={"X-Hub-Signature-256": "sha256=bad", "X-GitHub-Event": "issues"},
        )
    assert resp.status_code == 401
    assert app.state.org_repos_repo.notes == []
