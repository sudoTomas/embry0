"""Who reads each watched stack's state in the readiness check (``GET /api/v1/health/ready``).

The route is mounted without authentication: a monitor reads it with no key.
Its overall state, ``status`` and the checks that name nothing (``database``,
``watcher``), answers every caller. ``watcher_stacks`` names each watched
stack's repository, so it answers only a caller the authenticated routes
would let read, and a key with a repository allowlist only for the
repositories its list covers. A header that is not a live key, and a session
that does not pass, read as no key: 200 with the overall state, never a 401.

A key without a list, the master key and a session read what every caller
read before, byte for byte: what an AUTH_DEV_MODE app, where every caller
reads everything, answers for the same stacks.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.deps import bust_api_key_cache, bust_user_gate_cache
from embry0.api.session import SESSION_COOKIE, SessionData, encode_session, fold_tier
from embry0.config import Embry0Config
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.users import UserRow

MASTER = "master-key-" + "0" * 30
SECRET = "s" * 40
#: The watched stacks and their states: one broken, so the overall state is degraded.
STACKS = {"acme/web": "ok", "globex/gizmos": "invalid_logql", "acme/api": "unverified"}
#: What a caller without a key reads for :data:`STACKS`.
OVERALL = {"status": "degraded", "checks": {"database": "ok", "watcher": "invalid_logql"}}
#: Every name a stack's detail would show.
NAMES = ("acme", "globex", "web", "gizmos", "api")
PAST = datetime.now(UTC) - timedelta(days=1)


def _token(n: int) -> str:
    return f"key-{n:012d}"


def _key(n: int, *, scope: str = "admin", **fields: Any) -> ApiKeyRow:
    values: dict[str, Any] = {
        "id": f"key-id-{n:06d}",
        "user_id": None,
        "name": f"key-{n}",
        "prefix": _token(n)[:12],
        "scope": scope,
        "created_by": "tests",
        "created_at": None,
        "expires_at": None,
        "last_used_at": None,
        "revoked_at": None,
        "allowed_repos": None,
    }
    values.update(fields)
    return ApiKeyRow(**values)


#: A key without a list, of each scope a key can carry: each reads every stack.
OPEN = {scope: _token(n) for n, scope in enumerate(("read", "operate", "admin", "merge", "propose"), start=1)}
#: A read key covering acme/web, and an admin key covering a repository no stack watches.
LIMITED, LIMITED_ELSEWHERE = _token(11), _token(12)
#: Keys that are not live: revoked, expired, and owned by a disabled person.
REVOKED, EXPIRED, OWNER_OFF = _token(21), _token(22), _token(23)

KEYS: dict[str, ApiKeyRow] = {
    **{token: _key(n, scope=scope) for n, (scope, token) in enumerate(OPEN.items(), start=1)},
    LIMITED: _key(11, scope="read", allowed_repos=("acme/web",)),
    LIMITED_ELSEWHERE: _key(12, allowed_repos=("initech/portal",)),
    REVOKED: _key(21, revoked_at=PAST),
    EXPIRED: _key(22, expires_at=PAST),
    OWNER_OFF: _key(23, user_id="user-off"),
}


def _user(sub: str, *, disabled: bool = False, epoch: int = 0) -> UserRow:
    return UserRow(
        id=f"user-{sub}",
        auth0_sub=sub,
        email=f"{sub}@example.com",
        name=sub,
        tier="operator",
        disabled=disabled,
        session_epoch=epoch,
        login_count=1,
        first_login_at=None,
        last_login_at=None,
        created_at=None,
    )


#: The users the sessions below belong to, by Auth0 subject.
USERS = {
    "operator": _user("operator"),
    "admin": _user("admin"),
    "norole": _user("norole"),
    "disabled": _user("disabled", disabled=True),
    "stale": _user("stale", epoch=1),
}


class _Keys:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def get_by_hash(self, key_hash: str) -> ApiKeyRow | None:
        if self.fail:
            raise ConnectionError("the database is gone")
        return next((row for token, row in KEYS.items() if hash_token(token) == key_hash), None)

    async def touch_last_used(self, _key_id: str) -> None:
        return None


class _Users:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def get_by_sub(self, sub: str) -> UserRow | None:
        if self.fail:
            raise ConnectionError("the database is gone")
        return USERS.get(sub)

    async def get(self, user_id: str) -> UserRow | None:
        return _user("off", disabled=True) if user_id == "user-off" else None


class _OkDb:
    pool = object()

    async def fetchval(self, *_args: Any) -> int:
        return 1


def _config(*, dev_mode: bool = False) -> Embry0Config:
    sso = (
        {}
        if dev_mode
        else {
            "auth0_issuer": "https://tenant.example.com/",
            "auth0_client_id": "client-id",
            "auth0_client_secret": "client-secret",
            "session_secret": SECRET,
            "sso_public_origin": "https://embry0.example.com",
            "sso_roles_claim": "https://example.com/roles",
        }
    )
    with patch.dict(os.environ, {}, clear=True):
        return Embry0Config(
            _env_file=None, auth_dev_mode=dev_mode, webhook_dev_mode=False, api_key=MASTER, watcher_enabled=True, **sso
        )


def _app(*, dev_mode: bool = False, keys: _Keys | None = None, users: _Users | None = None) -> FastAPI:
    app = create_app(_config(dev_mode=dev_mode))
    app.state.db = _OkDb()
    app.state.watcher_services = {repo: SimpleNamespace(health=state, _repo=repo) for repo, state in STACKS.items()}
    app.state.api_keys_repo = keys or _Keys()
    app.state.users_repo = users or _Users()
    return app


def _session(sub: str, roles: list[str], *, epoch: int = 0) -> str:
    data = SessionData(sub=sub, email=f"{sub}@example.com", name=sub, roles=roles, tier=fold_tier(roles), epoch=epoch)
    return encode_session(SECRET, data)


async def _read(app: FastAPI, headers: Mapping[str, str] | None = None, cookie: str | None = None) -> httpx.Response:
    cookies = {SESSION_COOKIE: cookie} if cookie is not None else None
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers, cookies=cookies) as client:
        return await client.get("/api/v1/health/ready")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    bust_api_key_cache()
    bust_user_gate_cache()
    yield
    bust_api_key_cache()
    bust_user_gate_cache()


async def _every_stack() -> bytes:
    """What every caller read before: an AUTH_DEV_MODE app's answer, where every caller reads everything."""
    resp = await _read(_app(dev_mode=True))
    assert resp.status_code == 200, resp.text
    expected = {**OVERALL, "checks": {**OVERALL["checks"], "watcher_stacks": STACKS}}
    assert resp.json() == expected, f"the reference is every stack's state: {resp.text}"
    return resp.content


def _overall_alone(label: str, resp: httpx.Response) -> None:
    assert resp.status_code == 200, f"{label}: the readiness check answers a caller without a key: {resp.status_code}"
    named = [name for name in NAMES if name in resp.text]
    assert resp.json() == OVERALL and not named, (
        f"{label}: a caller without a key reads only the overall state, naming no repository: {resp.text}"
    )


NO_KEY: dict[str, dict[str, str]] = {
    "no header": {},
    "a bearer that is no key": _bearer("not-a-key"),
    "an empty bearer": {"Authorization": "Bearer "},
    "another scheme": {"Authorization": "Basic abc"},
    "a revoked key": _bearer(REVOKED),
    "an expired key": _bearer(EXPIRED),
    "a key whose owner is disabled": _bearer(OWNER_OFF),
}


@pytest.mark.parametrize("label", list(NO_KEY))
async def test_a_caller_without_a_live_key_reads_the_overall_state_alone(label: str) -> None:
    _overall_alone(label, await _read(_app(), NO_KEY[label]))


SESSIONS_THAT_DO_NOT_PASS = {
    "a session without an embry0 role": _session("norole", ["Sales"]),
    "a disabled person's session": _session("disabled", ["embry0-operator"]),
    "a session from before a sign-out everywhere": _session("stale", ["embry0-admin"], epoch=0),
    "a cookie that does not verify": _session("operator", ["embry0-operator"]) + "x",
}


@pytest.mark.parametrize("label", list(SESSIONS_THAT_DO_NOT_PASS))
async def test_a_session_that_does_not_pass_reads_as_no_key(label: str) -> None:
    _overall_alone(label, await _read(_app(), cookie=SESSIONS_THAT_DO_NOT_PASS[label]))


async def test_a_bearer_that_is_no_key_reads_as_no_key_beside_a_session() -> None:
    """As on every authenticated route, the header decides when there is one: a session does not make it a key."""
    resp = await _read(_app(), _bearer("not-a-key"), cookie=_session("operator", ["embry0-operator"]))
    _overall_alone("a bearer that is no key with an operator session", resp)


async def test_an_error_resolving_the_caller_reads_as_no_key() -> None:
    _overall_alone("the key store failing", await _read(_app(keys=_Keys(fail=True)), _bearer(OPEN["admin"])))
    _overall_alone(
        "the users store failing",
        await _read(_app(users=_Users(fail=True)), cookie=_session("operator", ["embry0-operator"])),
    )


CALLERS_WITHOUT_A_LIST: dict[str, tuple[dict[str, str], str | None]] = {
    "the master key": (_bearer(MASTER), None),
    **{f"a {scope} key without a list": (_bearer(token), None) for scope, token in OPEN.items()},
    "an operator session": ({}, _session("operator", ["embry0-operator"])),
    "an admin session": ({}, _session("admin", ["embry0-admin"])),
}


@pytest.mark.parametrize("label", list(CALLERS_WITHOUT_A_LIST))
async def test_a_caller_without_a_list_reads_every_stack_as_before(label: str) -> None:
    headers, cookie = CALLERS_WITHOUT_A_LIST[label]
    resp = await _read(_app(), headers, cookie)
    before = await _every_stack()
    assert (resp.status_code, resp.content) == (200, before), (
        f"{label} reads every stack's state, byte for byte as before: {resp.status_code} {resp.text}"
    )


async def test_a_key_with_a_list_reads_only_the_stacks_it_covers() -> None:
    resp = await _read(_app(), _bearer(LIMITED))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {**OVERALL, "checks": {**OVERALL["checks"], "watcher_stacks": {"acme/web": "ok"}}}, (
        f"a key covering acme/web reads the overall state and acme/web's stack alone: {resp.text}"
    )
    assert "gizmos" not in resp.text and "acme/api" not in resp.text, resp.text

    elsewhere = await _read(_app(), _bearer(LIMITED_ELSEWHERE))
    assert elsewhere.json() == {**OVERALL, "checks": {**OVERALL["checks"], "watcher_stacks": {}}}, (
        f"a key covering no watched repository reads the overall state and no stack: {elsewhere.text}"
    )


async def test_the_key_s_list_applies_beside_a_session() -> None:
    resp = await _read(_app(), _bearer(LIMITED), cookie=_session("admin", ["embry0-admin"]))
    assert resp.json()["checks"].get("watcher_stacks") == {"acme/web": "ok"}, (
        f"the key's list applies with an admin session beside it: {resp.text}"
    )


async def test_every_caller_reads_one_overall_state() -> None:
    """The overall state is the same for everyone: a key's list narrows the detail, never the state."""
    answers = [
        (await _read(_app(), headers)).json()
        for headers in ({}, _bearer(LIMITED), _bearer(LIMITED_ELSEWHERE), _bearer(MASTER))
    ]
    overall = {(a["status"], a["checks"]["database"], a["checks"]["watcher"]) for a in answers}
    assert overall == {("degraded", "ok", "invalid_logql")}, f"every caller reads one overall state: {answers}"


@pytest.mark.requires_postgres
async def test_real_keys_read_the_stacks_their_lists_cover(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app as production builds it, its keys minted and its stacks stored in a real database: a stack's state
    per watched row, as the startup builds one watcher per enabled stack."""
    from tests.fixtures.repository_writes import COVERED, MASTER, UNCOVERED, repository_writes_app

    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        watched = await app.state.watcher_stacks_repo.list_all(enabled_only=True)
        assert {stack.repo for stack in watched} >= {COVERED, UNCOVERED}, "both repositories have a watched stack"
        app.state.watcher_services = {stack.repo: SimpleNamespace(health="ok", _repo=stack.repo) for stack in watched}
        monkeypatch.setattr(app.state.config, "watcher_enabled", True)
        every = {stack.repo: "ok" for stack in watched}

        async def stacks(headers: Mapping[str, str]) -> Any:
            resp = await _read(app, headers)
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "ok" and resp.json()["checks"]["watcher"] == "ok", resp.text
            return resp.json()["checks"].get("watcher_stacks", "none shown")

        assert await stacks({}) == "none shown", "a caller without a key reads no stack"
        assert await stacks(_bearer("not-a-key")) == "none shown", "a bearer that is no key reads no stack"
        assert await stacks(_bearer(keys.limited)) == {COVERED: "ok"}, "the limited key reads its own stack alone"
        assert await stacks(_bearer(keys.open)) == every, "a key without a list reads every stack"
        assert await stacks(_bearer(MASTER)) == every, "the master key reads every stack"
