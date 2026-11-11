"""A key with a repository allowlist adds no secret to the deployment, on the real app over a real database.

Every stored credential's secret joins one scrub set in the orchestrator
(:func:`embry0.safety.redaction.register_known_secrets`), and every
repository's job events, agent texts, traces and log lines are redacted with
it. A secret is any text of eight characters or more, so a credential a key
creates or rotates changes what every repository's output reads, whatever the
credential is then bound to. A new account also joins every other caller's
repository discovery. Creating and rotating a credential is therefore the
deployment's, refused to a key with an allowlist as the deployment's writes are
(``test_deployment_writes_inventory.py``), and so is finishing a model
account's sign-in, a read that stores a credential bound for every
repository's jobs.

The app and its keys are :mod:`tests.fixtures.repository_writes`'.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.api.repo_allowlist import DEPLOYMENT
from embry0.safety.redaction import known_secret_values
from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, MASTER, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: The secrets each caller sends: words a job's output could hold, each long enough to join the scrub set.
CREATED, ROTATED, OPEN_CREATED = "createdbylimitedkey", "rotatedbylimitedkey", "createdbyunlistedkey"


def _refused_as_the_deployment(response: Any) -> bool:
    detail = response.json().get("detail", "") if response.status_code == 403 else ""
    return str(detail).startswith("repo_not_allowed") and DEPLOYMENT in str(detail)


async def _refusals(db: DatabasePool) -> int:
    return int(await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'apikey.repo_refused'"))


async def test_a_key_with_an_allowlist_adds_nothing_to_the_scrub_set(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limited key creates a credential, and rotates one only its own repository uses: both are refused as the
    deployment's, the scrub set and the stored credential are as they were, and each refusal is audited. A key
    without a list creating one adds its secret, so the check can tell."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        its = await call(
            app,
            "POST",
            "/api/v1/credentials",
            MASTER,
            {"kind": "forge_token", "provider": "github", "label": "its-forge", "secret": "z" * 24},
        )
        assert its.status_code == 201, its.text
        cid = str(its.json()["id"])
        bound = await call(
            app,
            "PUT",
            f"/api/v1/repos/{COVERED}/-/credentials",
            MASTER,
            {"bindings": [{"credential_id": cid, "role": "forge"}]},
        )
        assert bound.status_code == 200, bound.text
        stored = await db.fetchrow("SELECT secret_ciphertext, masked_preview FROM credentials WHERE id = $1", cid)
        known, refused, count = (
            set(known_secret_values()),
            await _refusals(db),
            await db.fetchval("SELECT count(*) FROM credentials"),
        )
        created = await call(
            app,
            "POST",
            "/api/v1/credentials",
            keys.limited,
            {"kind": "forge_token", "provider": "github", "label": "bot-forge", "secret": CREATED},
        )
        rotated = await call(app, "POST", f"/api/v1/credentials/{cid}/rotate", keys.limited, {"secret": ROTATED})
        after = set(known_secret_values())
        audited = await _refusals(db) - refused
        still = await db.fetchrow("SELECT secret_ciphertext, masked_preview FROM credentials WHERE id = $1", cid)
        counted = await db.fetchval("SELECT count(*) FROM credentials")
        renamed = await call(app, "PUT", f"/api/v1/credentials/{cid}", keys.limited, {"label": "its-forge-renamed"})
        opened = await call(
            app,
            "POST",
            "/api/v1/credentials",
            keys.open,
            {"kind": "forge_token", "provider": "github", "label": "open-forge", "secret": OPEN_CREATED},
        )
        with_open = set(known_secret_values())
    assert _refused_as_the_deployment(created), f"creating: {created.status_code} {created.text}"
    assert _refused_as_the_deployment(rotated), f"rotating its own repository's: {rotated.status_code} {rotated.text}"
    assert after == known and {CREATED, ROTATED}.isdisjoint(after), (
        f"the limited key changed the scrub set: added {sorted(after - known)}"
    )
    assert (dict(still), counted) == (dict(stored), count), "a refused create or rotate wrote a credential"
    assert audited == 2, f"each refusal is audited: {audited} rows"
    assert renamed.status_code == 200, (
        f"a credential only its repository uses stays the key's to rename: {renamed.text}"
    )
    assert opened.status_code == 201 and OPEN_CREATED in with_open, (
        f"a key without a list adds its secret, so the check can fire: {opened.status_code} {opened.text}"
    )


class _SignedIn:
    """An xAI device login the person has finished: the next poll answers with the account's tokens."""

    def __init__(self) -> None:
        self.polled = 0

    async def poll(self, _device_code: str) -> dict[str, Any]:
        self.polled += 1
        return {
            "status": "done",
            "refresh_token": "xai-refresh-000000000001",
            "client_id": "client-000000000001",
            "token_endpoint": "https://auth.x.ai/oauth2/token",
        }


async def test_a_key_with_an_allowlist_does_not_finish_a_model_account_sign_in(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /credentials/xai/device-login/{id}`` stores the signed-in account and binds it for every repository. To
    the limited key it answers the deployment's refusal before the login is looked up, the same for a login that does
    not exist, and the login stays pending; a key without a list finishes it."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        login = _SignedIn()
        app.state.xai_device_logins = {
            "login-000000000001": {
                "label": "grok-team",
                "device_code": "device-000000000001",
                "expires_at": datetime.now(UTC) + timedelta(minutes=10),
                "login": login,
                "http": None,
            }
        }
        refused = await _refusals(db)
        pending = await call(app, "GET", "/api/v1/credentials/xai/device-login/login-000000000001", keys.limited)
        missing = await call(app, "GET", "/api/v1/credentials/xai/device-login/login-000000000009", keys.limited)
        audited = await _refusals(db) - refused
        left = (
            login.polled,
            sorted(app.state.xai_device_logins),
            await db.fetchval("SELECT count(*) FROM credentials WHERE provider = 'xai'"),
            await db.fetchval("SELECT count(*) FROM credential_bindings WHERE scope_kind = 'global'"),
        )
        finished = await call(app, "GET", "/api/v1/credentials/xai/device-login/login-000000000001", keys.open)
        stored = await db.fetchval("SELECT count(*) FROM credentials WHERE provider = 'xai'")
    assert _refused_as_the_deployment(pending), f"a pending login: {pending.status_code} {pending.text}"
    assert (missing.status_code, missing.content) == (pending.status_code, pending.content), (
        f"a missing login answers as a pending one: {missing.text} against {pending.text}"
    )
    assert audited == 2, f"each refusal is audited: {audited} rows"
    assert left == (0, ["login-000000000001"], 0, 0), f"the refused poll finished the login: {left}"
    assert finished.status_code == 200 and finished.json()["status"] == "done", finished.text
    assert stored == 1, "a key without a list finishes the sign-in"
