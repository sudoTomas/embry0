"""/api/v1/credentials — tiers, masking, validation, resolve dry run, audit."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.credentials.resolver import CredentialResolver
from embry0.storage.repositories.credentials import BindingValidationError, CredentialBoundError
from tests.fixtures.routes import app_routes


class FakeSecrets:
    async def encrypt(self, p):
        return "enc:" + p

    async def decrypt(self, c):
        return c.removeprefix("enc:")


class MemRepo:
    """In-memory CredentialsRepository with the same method surface Task 3 defines."""

    def __init__(self):
        self.rows, self.bindings, self.uses = {}, {}, []

    async def create(self, **kw):
        if any(r["provider"] == kw["provider"] and r["label"] == kw["label"] for r in self.rows.values()):
            raise asyncpg.UniqueViolationError("duplicate key value violates unique constraint")
        row = dict(
            kw,
            id=f"cred-{len(self.rows) + 1:012x}",
            status="ok",
            status_reason=None,
            quarantined_until=None,
            verified_login=None,
            created_at="2026-09-29T00:00:00Z",
            updated_at="2026-09-29T00:00:00Z",
            rotated_at=None,
            last_used_at=None,
            public_material=None,
        )
        self.rows[row["id"]] = row
        return {k: v for k, v in row.items() if k != "secret_ciphertext"}

    async def get(self, cid):
        return dict(self.rows[cid]) if cid in self.rows else None

    async def find_by_label(self, provider, label):
        return next((dict(r) for r in self.rows.values() if r["provider"] == provider and r["label"] == label), None)

    async def all_ciphertexts(self):
        return [{"id": r["id"], "secret_ciphertext": r["secret_ciphertext"]} for r in self.rows.values()]

    async def list_all(self):
        return [
            dict(
                {k: v for k, v in r.items() if k != "secret_ciphertext"},
                binding_count=sum(1 for bs in self.bindings.values() for b in bs if b["credential_id"] == r["id"]),
            )
            for r in self.rows.values()
        ]

    async def update_meta(self, cid, *, label=None, **author):
        # Mirrors the repository: label None keeps; an author kwarg passed is
        # written, None included (clears); one not passed is kept (#440).
        if cid not in self.rows:
            return None
        if label is not None:
            self.rows[cid]["label"] = label
        self.rows[cid].update(author)
        return await self.get(cid)

    async def rotate(self, cid, *, secret_ciphertext, masked_preview):
        if cid not in self.rows:
            return None
        self.rows[cid].update(secret_ciphertext=secret_ciphertext, masked_preview=masked_preview, rotated_at="now")
        return await self.get(cid)

    async def set_status(self, cid, status, *, reason=None, quarantined_until=None):
        # Mirrors CredentialsRepository.set_status: ok clears reason and the window.
        self.rows[cid].update(
            status=status,
            status_reason=None if status == "ok" else reason,
            quarantined_until=quarantined_until if status == "quarantined" else None,
        )
        return await self.get(cid)

    async def set_verified_login(self, cid, login):
        self.rows[cid]["verified_login"] = login

    async def delete(self, cid):
        if any(b["credential_id"] == cid for bs in self.bindings.values() for b in bs):
            raise CredentialBoundError("bound")
        return self.rows.pop(cid, None) is not None

    async def bindings_for_scope(self, k, v):
        return list(self.bindings.get((k, v), []))

    async def bindings_for_credential(self, cid):
        return [b for bs in self.bindings.values() for b in bs if b["credential_id"] == cid]

    async def replace_bindings(self, k, v, items, *, created_by, repo_forge=None):
        for i in items:
            row = self.rows[i["credential_id"]]
            if i["role"] == "forge" and repo_forge and row["provider"] != repo_forge:
                raise BindingValidationError(
                    f"repo is on forge '{repo_forge}' but credential '{row['label']}' is for '{row['provider']}'"
                )
        self.bindings[(k, v)] = [
            dict(
                i,
                scope_kind=k,
                scope_value=v,
                provider=self.rows[i["credential_id"]]["provider"],
                label=self.rows[i["credential_id"]]["label"],
                status="ok",
            )
            for i in items
        ]
        return self.bindings[(k, v)]

    async def candidates(self, *, scope_kind, scope_value, role, provider):
        return [
            dict(self.rows[b["credential_id"]], pinned=b.get("pinned", False), priority=b.get("priority", 0))
            for b in self.bindings.get((scope_kind, scope_value), [])
            if b["role"] == role and b["provider"] == provider
        ]

    async def any_for_provider(self, provider):
        return any(r["provider"] == provider for r in self.rows.values())

    async def record_use(self, **kw):
        self.uses.append(kw)

    async def finish_uses(self, job_id, outcome):
        return 0

    async def uses_for_job(self, job_id):
        return []


@pytest.fixture(autouse=True)
def _reset_scrub_set():
    """Every create registers its secret process-wide; leave nothing behind for other modules."""
    from embry0.safety.redaction import register_known_secrets

    yield
    register_known_secrets([], reset=True)


@pytest.fixture
def app():
    config = Embry0Config(
        _env_file=None,
        auth_dev_mode=True,
        webhook_dev_mode=True,
        gitlab_url="https://gitlab.example.com",
        github_token="",
        gitlab_token="",
    )
    app = create_app(config)
    app.state.credentials_repo = MemRepo()
    app.state.secrets_provider = FakeSecrets()
    app.state.credential_resolver = CredentialResolver(app.state.credentials_repo, FakeSecrets(), config, environ={})
    app.state.repos_repo = MagicMock(
        get=AsyncMock(return_value={"repo": "acme/widgets", "forge": "github"}),
        register=AsyncMock(return_value={"repo": "acme/widgets", "forge": "github"}),
        forge_for=AsyncMock(return_value="github"),
    )
    app.state.db = None
    app.state.audit_events = []
    return app


@pytest.fixture
async def client(app):
    # X-Requested-With: the CSRF guard refuses a state-changing request without it.
    headers = {"X-Requested-With": "XMLHttpRequest"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as c:
        yield c


async def _create(client, **over):
    body = {
        "kind": "forge_token",
        "provider": "github",
        "label": "alice",
        "secret": "ghp_abcdefghijklmnop",
        "git_author_name": "Alice",
        "git_author_email": "alice@example.com",
    }
    body.update(over)
    return await client.post("/api/v1/credentials", json=body)


async def test_create_lists_masked_never_secret(client):
    with patch("embry0.api.v1.credentials.emit_audit", new=AsyncMock()) as audit:
        r = await _create(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["masked_preview"] == "ghp_••••••••mnop" and "secret" not in body and "secret_ciphertext" not in body
    listed = (await client.get("/api/v1/credentials")).json()
    assert listed[0]["label"] == "alice" and "ghp_abcdefghijklmnop" not in r.text + str(listed)
    audit.assert_awaited_once()
    assert audit.await_args.args[1] == "credential.created"
    assert "ghp_abcdefghijklmnop" not in str(audit.await_args.kwargs["details"])


async def test_create_strips_whitespace_and_rejects_blank(client):
    """Review focus 4."""
    r = await _create(client, secret="  ghp_abcdefghijklmnop\n")
    assert r.status_code == 201 and r.json()["masked_preview"] == "ghp_••••••••mnop"
    r = await _create(client, label="blank", secret="   \n")
    assert r.status_code == 422


async def test_create_rejects_implausible_email(client):
    r = await _create(client, git_author_email="not an email")
    assert r.status_code == 422 and "git_author_email" in r.text


async def test_host_must_match_forge(client):
    r = await _create(client, host="ghe.example.com")
    assert r.status_code == 422 and "github.com" in r.text
    r = await _create(client, provider="gitlab", host="gitlab.com", label="gl")
    assert r.status_code == 422 and "gitlab.example.com" in r.text
    r = await _create(client, provider="gitlab", label="gl2")
    assert r.status_code == 201 and r.json()["host"] == "gitlab.example.com"


async def test_duplicate_label_is_409(client):
    assert (await _create(client)).status_code == 201
    assert (await _create(client)).status_code == 409


async def test_credential_mutations_invalidate_the_resolver_cache(client, app):
    """#439: a binding or secret change must drop the forge-token cache."""
    calls = {"n": 0}
    original = app.state.credential_resolver.invalidate

    def _spy():
        calls["n"] += 1
        original()

    app.state.credential_resolver.invalidate = _spy
    cid = (await _create(client)).json()["id"]
    after_create = calls["n"]
    assert after_create >= 1, "create did not invalidate the resolver cache"
    r = await client.put(
        "/api/v1/repos/acme/widgets/-/credentials", json={"bindings": [{"credential_id": cid, "role": "forge"}]}
    )
    assert r.status_code == 200, r.text
    assert calls["n"] > after_create, "replacing repo bindings did not invalidate the resolver cache"
    after_repo = calls["n"]
    r = await client.put(
        "/api/v1/credentials/bindings",
        json={"scope_kind": "global", "scope_value": "", "bindings": [{"credential_id": cid, "role": "forge"}]},
    )
    assert r.status_code == 200, r.text
    assert calls["n"] > after_repo, "replacing scoped bindings did not invalidate the resolver cache"


async def test_bind_repo_then_resolve_explains(client, app):
    cid = (await _create(client)).json()["id"]
    r = await client.put(
        "/api/v1/repos/acme/widgets/-/credentials", json={"bindings": [{"credential_id": cid, "role": "forge"}]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["bindings"][0]["credential_id"] == cid
    assert r.json()["effective"]["forge"]["label"] == "alice"
    r = await client.get("/api/v1/credentials/resolve", params={"repo": "acme/widgets", "role": "forge"})
    assert r.status_code == 200
    assert r.json()["label"] == "alice" and r.json()["trace"][0].startswith("repo:acme/widgets")
    assert "secret" not in r.json()


async def test_mixed_case_repo_binding_is_stored_lowercase(client, app):
    """The resolver looks the repo scope up lowercased; the API writes it that way."""
    cid = (await _create(client)).json()["id"]
    r = await client.put(
        "/api/v1/repos/Acme/Widgets/-/credentials", json={"bindings": [{"credential_id": cid, "role": "forge"}]}
    )
    assert r.status_code == 200, r.text
    assert list(app.state.credentials_repo.bindings) == [("repo", "acme/widgets")]
    r = await client.get("/api/v1/credentials/resolve", params={"repo": "Acme/Widgets", "role": "forge"})
    assert r.status_code == 200 and r.json()["label"] == "alice"


async def test_bad_repo_path_is_422(client):
    r = await client.get("/api/v1/credentials/resolve", params={"repo": "no-slash", "role": "forge"})
    assert r.status_code == 422


async def test_bind_wrong_forge_is_422(client, app):
    cid = (await _create(client, provider="gitlab", label="gl")).json()["id"]
    r = await client.put(
        "/api/v1/repos/acme/widgets/-/credentials", json={"bindings": [{"credential_id": cid, "role": "forge"}]}
    )
    assert r.status_code == 422 and "gitlab" in r.text


async def test_delete_refused_while_bound_then_ok(client):
    cid = (await _create(client)).json()["id"]
    await client.put(
        "/api/v1/credentials/bindings",
        json={"scope_kind": "global", "scope_value": "", "bindings": [{"credential_id": cid, "role": "forge"}]},
    )
    assert (await client.delete(f"/api/v1/credentials/{cid}")).status_code == 409
    await client.put("/api/v1/credentials/bindings", json={"scope_kind": "global", "scope_value": "", "bindings": []})
    assert (await client.delete(f"/api/v1/credentials/{cid}")).status_code == 204
    assert (await client.delete(f"/api/v1/credentials/{cid}")).status_code == 404


async def test_rotate_update_and_disable(client, app):
    cid = (await _create(client)).json()["id"]
    r = await client.post(f"/api/v1/credentials/{cid}/rotate", json={"secret": "ghp_zzzzzzzzzzzzzzzz"})
    assert r.status_code == 200 and r.json()["masked_preview"] == "ghp_••••••••zzzz"
    # Bind it first, so the 404 below is caused by the disable and not by a missing binding.
    await client.put(
        "/api/v1/repos/acme/widgets/-/credentials", json={"bindings": [{"credential_id": cid, "role": "forge"}]}
    )
    r = await client.get("/api/v1/credentials/resolve", params={"repo": "acme/widgets", "role": "forge"})
    assert r.status_code == 200 and r.json()["id"] == cid
    r = await client.put(f"/api/v1/credentials/{cid}", json={"status": "disabled", "label": "alice-old"})
    assert r.json()["status"] == "disabled" and r.json()["label"] == "alice-old"
    r = await client.get("/api/v1/credentials/resolve", params={"repo": "acme/widgets", "role": "forge"})
    assert r.status_code == 404 and "disabled" in r.text


async def test_unknown_credential_is_404(client):
    missing = "cred-00000000ffff"
    assert (await client.put(f"/api/v1/credentials/{missing}", json={"label": "x"})).status_code == 404
    r = await client.post(f"/api/v1/credentials/{missing}/rotate", json={"secret": "ghp_zzzzzzzzzzzzzzzz"})
    assert r.status_code == 404
    assert (await client.post(f"/api/v1/credentials/{missing}/verify")).status_code == 404


async def test_verify_stores_login_and_502_on_rejection(client, app):
    cid = (await _create(client)).json()["id"]
    with patch("embry0.api.v1.credentials._probe_login", new=AsyncMock(return_value="alice-gh")) as probe:
        r = await client.post(f"/api/v1/credentials/{cid}/verify")
    assert r.status_code == 200 and r.json()["verified_login"] == "alice-gh"
    assert probe.await_args.args[0] == "ghp_abcdefghijklmnop"  # the decrypted secret reaches the probe
    with patch("embry0.api.v1.credentials._probe_login", new=AsyncMock(side_effect=RuntimeError("HTTP 401"))):
        r = await client.post(f"/api/v1/credentials/{cid}/verify")
    assert r.status_code == 502 and "401" in r.text


async def test_import_env_endpoint(client, app, monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith(("GITHUB_TOKEN__", "GITLAB_TOKEN__")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GITHUB_TOKEN__ACME", "ghp_fromenv12345678")
    r = await client.post("/api/v1/credentials/import-env", json={"replace": False})
    assert r.status_code == 200 and r.json()["created"] == ["github/acme"]


async def test_unregistered_repo_binding_registers_it(client, app):
    app.state.repos_repo.get = AsyncMock(return_value=None)
    cid = (await _create(client)).json()["id"]
    r = await client.put(
        "/api/v1/repos/acme/widgets/-/credentials", json={"bindings": [{"credential_id": cid, "role": "forge"}]}
    )
    assert r.status_code == 200
    app.state.repos_repo.register.assert_awaited()


async def test_scrub_set_refreshed_after_create(client):
    from embry0.safety import redaction

    await _create(client, secret="ghp_scrubmeplease1234")
    assert "ghp_scrubmeplease1234" in redaction._KNOWN_SECRETS


def test_mutations_are_admin_tier_reads_are_not(app):
    from embry0.api.deps import _admin_dependency

    admin = {
        ("POST", "/api/v1/credentials"),
        ("PUT", "/api/v1/credentials/{credential_id}"),
        ("POST", "/api/v1/credentials/{credential_id}/rotate"),
        ("DELETE", "/api/v1/credentials/{credential_id}"),
        ("POST", "/api/v1/credentials/{credential_id}/verify"),
        ("PUT", "/api/v1/repos/{full_repo:repo}/-/credentials"),
        # Its old path, without /-/, until repo_old_paths.OLD_PATHS_REMOVED_IN (#257).
        ("PUT", "/api/v1/repos/{full_repo:repo}/credentials"),
        ("PUT", "/api/v1/credentials/bindings"),
        ("POST", "/api/v1/credentials/import-env"),
        ("POST", "/api/v1/credentials/xai/device-login"),
        ("GET", "/api/v1/credentials/xai/device-login/{login_id}"),
    }
    operate = {
        ("GET", "/api/v1/credentials"),
        ("GET", "/api/v1/credentials/resolve"),
        ("GET", "/api/v1/repos/{full_repo:repo}/-/credentials"),
        ("GET", "/api/v1/repos/{full_repo:repo}/credentials"),
        ("GET", "/api/v1/credentials/bindings"),
    }
    seen: dict[tuple[str, str], bool] = {}
    for route in app_routes(app):
        if "credentials" not in route.path:
            continue
        assert route.dependant is not None and route.methods is not None, f"{route.path} is not an API route"
        calls = {d.call for d in route.dependant.dependencies}
        for method in route.methods:
            seen[(method, route.path)] = _admin_dependency in calls
    assert {k for k, v in seen.items() if v} == admin
    assert {k for k, v in seen.items() if not v} == operate


async def test_verify_never_echoes_the_secret(client, app):
    """An httpx/h11 error quotes the header it refused, token included; the 502 must not."""
    cid = (await _create(client)).json()["id"]
    secret = "ghp_abcdefghijklmnop"
    for exc in (Exception(f"Illegal header value b'Bearer {secret}'"), RuntimeError(f"boom {secret}")):
        with patch("embry0.api.v1.credentials._probe_login", new=AsyncMock(side_effect=exc)):
            r = await client.post(f"/api/v1/credentials/{cid}/verify")
        assert r.status_code == 502, r.text
        assert secret not in r.text, f"verify echoed the token: {r.text}"
        assert type(exc).__name__ in r.text


async def test_verify_undecryptable_is_409(client, app):
    from cryptography.fernet import InvalidToken

    cid = (await _create(client)).json()["id"]
    app.state.secrets_provider.decrypt = AsyncMock(side_effect=InvalidToken())
    r = await client.post(f"/api/v1/credentials/{cid}/verify")
    assert r.status_code == 409 and "decryption_failed" in r.text


@pytest.mark.parametrize("bad", ["ghp_SECRET\rVALUE", "ghp_SECRET\tVALUE", "ghp_SECRET VALUE", "ghp_SECRET\x00VALUE"])
async def test_secret_with_whitespace_or_control_chars_is_422(client, bad):
    r = await _create(client, secret=bad)
    assert r.status_code == 422, r.text
    assert bad not in r.json()["detail"].__repr__() and "ghp_SECRET" not in r.text, "422 echoed the secret"
    cid = (await _create(client, label="ok")).json()["id"]
    r = await client.post(f"/api/v1/credentials/{cid}/rotate", json={"secret": bad})
    assert r.status_code == 422, r.text


async def test_owner_binding_needs_a_forge_prefix(client):
    cid = (await _create(client)).json()["id"]
    body = {"scope_kind": "owner", "scope_value": "acme-corp", "bindings": [{"credential_id": cid, "role": "forge"}]}
    r = await client.put("/api/v1/credentials/bindings", json=body)
    assert r.status_code == 422 and "github:" in r.text, r.text


async def test_owner_binding_prefix_must_match_credential_provider(client):
    cid = (await _create(client)).json()["id"]
    body = {"scope_kind": "owner", "scope_value": "gitlab:acme", "bindings": [{"credential_id": cid, "role": "forge"}]}
    r = await client.put("/api/v1/credentials/bindings", json=body)
    assert r.status_code == 422 and "gitlab" in r.text, r.text
    body["scope_value"] = "github:acme"
    assert (await client.put("/api/v1/credentials/bindings", json=body)).status_code == 200


async def test_host_binding_must_be_the_forge_host(client):
    cid = (await _create(client)).json()["id"]
    body = {
        "scope_kind": "host",
        "scope_value": "ghe.example.com",
        "bindings": [{"credential_id": cid, "role": "forge"}],
    }
    r = await client.put("/api/v1/credentials/bindings", json=body)
    assert r.status_code == 422 and "github.com" in r.text, r.text
    body["scope_value"] = " GitHub.com "
    r = await client.put("/api/v1/credentials/bindings", json=body)
    assert r.status_code == 200, r.text
    r = await client.get("/api/v1/credentials/bindings", params={"scope_kind": "host", "scope_value": " github.com "})
    assert [b["credential_id"] for b in r.json()["bindings"]] == [cid]


async def test_status_only_update_audits_status_alone(client):
    cid = (await _create(client)).json()["id"]
    with patch("embry0.api.v1.credentials.emit_audit", new=AsyncMock()) as audit:
        await client.put(f"/api/v1/credentials/{cid}", json={"status": "disabled"})
    assert [c.args[1] for c in audit.await_args_list] == ["credential.status_changed"]


async def test_validation_error_never_echoes_the_secret(client):
    """FastAPI's default 422 echoes the whole body as ``input`` when a field is missing."""
    r = await client.post(
        "/api/v1/credentials", json={"provider": "github", "label": "x", "secret": "ghp_echoedsecret1234"}
    )
    assert r.status_code == 422 and "kind" in r.text
    assert "ghp_echoedsecret1234" not in r.text, r.text


_ANTHROPIC_KEY = "sk-ant-api03-abcdefghij"


async def test_create_llm_api_key_defaults_the_provider_host(client):
    r = await _create(
        client, kind="llm_api_key", provider="anthropic", label="claude-a", secret=_ANTHROPIC_KEY, host=""
    )
    assert r.status_code == 201, r.text
    assert r.json()["host"] == "api.anthropic.com"
    assert r.json()["kind"] == "llm_api_key"
    assert _ANTHROPIC_KEY not in r.text


async def test_kind_and_provider_must_agree(client):
    forge_on_llm = await _create(client, kind="forge_token", provider="anthropic", secret=_ANTHROPIC_KEY)
    assert forge_on_llm.status_code == 422, forge_on_llm.text
    assert "forge_token" in forge_on_llm.text and "github" in forge_on_llm.text
    llm_on_forge = await _create(client, kind="llm_api_key", provider="github", secret=_ANTHROPIC_KEY)
    assert llm_on_forge.status_code == 422, llm_on_forge.text
    assert "llm_api_key" in llm_on_forge.text


async def test_xai_oauth_create_points_at_device_login(client):
    r = await _create(client, kind="llm_oauth", provider="xai", label="grok-a", secret="refresh-token-value")
    assert r.status_code == 422, r.text
    assert "device-code" in r.text
    assert "refresh-token-value" not in r.text


async def test_non_default_llm_host_is_422(client):
    r = await _create(
        client, kind="llm_api_key", provider="anthropic", label="claude-a", secret=_ANTHROPIC_KEY, host="github.com"
    )
    assert r.status_code == 422, r.text
    assert "api.anthropic.com" in r.text


async def test_resolve_llm_requires_a_provider(client):
    r = await client.get("/api/v1/credentials/resolve", params={"repo": "acme/widgets", "role": "llm"})
    assert r.status_code == 422, r.text
    assert "provider" in r.text


async def test_resolve_llm_returns_the_pool_member(client):
    created = await _create(client, kind="llm_api_key", provider="anthropic", label="claude-a", secret=_ANTHROPIC_KEY)
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    bound = await client.put(
        "/api/v1/repos/acme/widgets/-/credentials", json={"bindings": [{"credential_id": cid, "role": "llm"}]}
    )
    assert bound.status_code == 200, bound.text
    r = await client.get(
        "/api/v1/credentials/resolve", params={"repo": "acme/widgets", "role": "llm", "provider": "anthropic"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == cid and body["label"] == "claude-a" and body["source"] == "registry"
    assert body["provider"] == "anthropic"
    assert _ANTHROPIC_KEY not in r.text


async def test_repo_credentials_effective_llm_omits_a_missing_provider(client):
    created = await _create(client, kind="llm_api_key", provider="anthropic", label="claude-a", secret=_ANTHROPIC_KEY)
    cid = created.json()["id"]
    await client.put(
        "/api/v1/repos/acme/widgets/-/credentials", json={"bindings": [{"credential_id": cid, "role": "llm"}]}
    )
    r = await client.get("/api/v1/repos/acme/widgets/-/credentials")
    assert r.status_code == 200, r.text
    llm = r.json()["effective"]["llm"]
    assert llm["anthropic"]["id"] == cid and llm["anthropic"]["label"] == "claude-a"
    assert "xai" not in llm
    assert _ANTHROPIC_KEY not in r.text


async def test_verify_llm_api_key_stores_ok_and_502_hides_the_secret(client):
    from embry0.services.provider_probe import ProbeCheck

    created = await _create(client, kind="llm_api_key", provider="anthropic", label="claude-a", secret=_ANTHROPIC_KEY)
    cid = created.json()["id"]
    ok = ProbeCheck("api_key", "ok", "authenticated and reachable")
    with patch("embry0.services.provider_probe.probe_llm_secret", new=AsyncMock(return_value=ok)) as probe:
        r = await client.post(f"/api/v1/credentials/{cid}/verify")
    assert r.status_code == 200, r.text
    assert r.json()["verified_login"] == "ok"
    assert probe.await_args.args[:3] == ("anthropic", "llm_api_key", _ANTHROPIC_KEY)
    rejected = ProbeCheck("api_key", "unauthorized", "credential rejected (HTTP 401)")
    with patch("embry0.services.provider_probe.probe_llm_secret", new=AsyncMock(return_value=rejected)):
        r = await client.post(f"/api/v1/credentials/{cid}/verify")
    assert r.status_code == 502, r.text
    assert _ANTHROPIC_KEY not in r.text


async def test_verify_xai_oauth_is_422_until_the_refresher_exists(client, app):
    """Device-login creates this row; POST /credentials refuses it. Seed the double directly."""
    repo = app.state.credentials_repo
    row = await repo.create(
        kind="llm_oauth",
        provider="xai",
        host="api.x.ai",
        label="grok-a",
        secret_ciphertext="enc:refresh-token-value",
        masked_preview="refr••••alue",
    )
    r = await client.post(f"/api/v1/credentials/{row['id']}/verify")
    assert r.status_code == 422, r.text
    assert "refresher" in r.text
    assert "refresh-token-value" not in r.text


async def test_verify_xai_oauth_probes_the_registry_access_token(client, app):
    """The refresh payload stays in the row. Verify probes with the minted access token."""
    from embry0.services.provider_probe import ProbeCheck

    repo = app.state.credentials_repo
    row = await repo.create(
        kind="llm_oauth",
        provider="xai",
        host="api.x.ai",
        label="grok-a",
        secret_ciphertext="enc:refresh-token-value",
        masked_preview="refr••••alue",
    )
    access = "access-token-from-registry"

    class _Registry:
        def __init__(self) -> None:
            self.ids: list[str] = []

        async def refresh_credential(self, credential_id: str) -> tuple[str, str]:
            self.ids.append(credential_id)
            return (access, "2030-01-01T00:00:00+00:00")

    registry = _Registry()
    app.state.xai_refresher_registry = registry
    ok = ProbeCheck("oauth", "ok", "authenticated and reachable")
    with patch("embry0.services.provider_probe.probe_llm_secret", new=AsyncMock(return_value=ok)) as probe:
        r = await client.post(f"/api/v1/credentials/{row['id']}/verify")
    assert r.status_code == 200, r.text
    assert r.json()["verified_login"] == "ok"
    assert registry.ids == [row["id"]]
    assert probe.await_args.args[:3] == ("xai", "llm_oauth", access)
    assert "refresh-token-value" not in r.text
    assert access not in r.text


async def test_status_ok_clears_quarantine(client, app):
    cid = (await _create(client)).json()["id"]
    app.state.credentials_repo.rows[cid].update(
        status="quarantined", status_reason="rate_limited", quarantined_until="2026-10-01T00:00:00+00:00"
    )
    r = await client.put(f"/api/v1/credentials/{cid}", json={"status": "ok"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["quarantined_until"] is None
    assert r.json()["status_reason"] is None


# Device-code login. POST /credentials still refuses xAI llm_oauth; these routes create the row.

_DEVICE_CODE = "device-code-1"
_USER_CODE = "ABCD-EFGH"
_REFRESH = "refresh-token-value"
_ACCESS = "access-token-not-stored"
_VERIFY_URI = "https://auth.x.ai/device?user_code=ABCD-EFGH"
_TOKEN_ENDPOINT = "https://auth.x.ai/oauth2/token"
_DEVICE_CLIENT = "b1a00492-073a-47ea-816f-4c329264a828"


class _DeviceLogin:
    """Stand-in for XaiDeviceLogin. The route must call start/poll; this object records that."""

    def __init__(self, *, expires_in: int = 600, poll_result: dict | None = None, poll_error: Exception | None = None):
        self.expires_in = expires_in
        self.poll_result = poll_result
        self.poll_error = poll_error
        self.client_id: str | None = None
        self.polled: str | None = None

    async def start(self, client_id: str) -> dict:
        self.client_id = client_id
        return {
            "device_code": _DEVICE_CODE,
            "user_code": _USER_CODE,
            "verification_uri": "https://auth.x.ai/device",
            "verification_uri_complete": _VERIFY_URI,
            "interval": 5,
            "expires_in": self.expires_in,
        }

    async def poll(self, device_code: str) -> dict:
        self.polled = device_code
        if self.poll_error is not None:
            raise self.poll_error
        if self.poll_result is not None:
            return self.poll_result
        return {"status": "pending", "interval": 5}


def _patch_device_login(login: _DeviceLogin):
    return patch("embry0.execution.xai_device_login.XaiDeviceLogin", return_value=login)


async def _close_device_http(app) -> None:
    for item in getattr(app.state, "xai_device_logins", {}).values():
        http = item.get("http")
        if http is not None:
            await http.aclose()


async def test_xai_device_login_start_hides_the_device_code(client, app):
    """The browser gets the user code. device_code stays on the server."""
    login = _DeviceLogin()
    with _patch_device_login(login):
        r = await client.post("/api/v1/credentials/xai/device-login", json={"label": "grok-a"})
    assert r.status_code == 200, f"device-login start must return the user code, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["user_code"] == _USER_CODE, "device-login start must return the user code"
    assert body["verification_uri_complete"] == _VERIFY_URI
    assert body["interval"] == 5, "device-login start must return the poll interval"
    assert body["login_id"] and body["login_id"] != _DEVICE_CODE
    assert "expires_at" in body
    assert _DEVICE_CODE not in r.text, "device_code must not leave the server"
    assert login.client_id == _DEVICE_CLIENT, "start must use the public device client, not a request field"
    pending = app.state.xai_device_logins[body["login_id"]]
    assert pending["device_code"] == _DEVICE_CODE
    assert pending["label"] == "grok-a"
    await _close_device_http(app)


async def test_xai_device_login_body_is_only_the_label(client):
    r = await client.post(
        "/api/v1/credentials/xai/device-login",
        json={"label": "grok-a", "secret": _REFRESH, "client_id": "not-the-public-client"},
    )
    assert r.status_code == 422, f"device-login body is only label, got {r.status_code}: {r.text}"
    assert _REFRESH not in r.text, "a rejected device-login body must not echo the extra secret"


async def test_xai_device_login_poll_pending_does_not_create_a_row(client, app):
    login = _DeviceLogin()
    with _patch_device_login(login):
        started = await client.post("/api/v1/credentials/xai/device-login", json={"label": "grok-a"})
        assert started.status_code == 200, started.text
        login_id = started.json()["login_id"]
        r = await client.get(f"/api/v1/credentials/xai/device-login/{login_id}")
    assert r.status_code == 200, f"pending device-login must stay pending, got {r.status_code}: {r.text}"
    assert r.json() == {"status": "pending"}, "pending device-login returns only status"
    assert login.polled == _DEVICE_CODE, "pending poll must use the server-side device code"
    assert _DEVICE_CODE not in r.text and _REFRESH not in r.text
    assert app.state.credentials_repo.rows == {}, "a pending poll must not create a credential"
    await _close_device_http(app)


async def test_xai_device_login_done_creates_oauth_row_and_keeps_other_global_bindings(client, app):
    """A finished login writes the refresh payload and adds one global xAI binding beside anthropic."""
    import json

    from embry0.safety.redaction import known_secret_values

    created = await _create(client, kind="llm_api_key", provider="anthropic", label="claude-a", secret=_ANTHROPIC_KEY)
    assert created.status_code == 201, created.text
    anthropic_id = created.json()["id"]
    bound = await client.put(
        "/api/v1/credentials/bindings",
        json={
            "scope_kind": "global",
            "scope_value": "",
            "bindings": [{"credential_id": anthropic_id, "role": "llm", "priority": 1}],
        },
    )
    assert bound.status_code == 200, bound.text
    login = _DeviceLogin(
        poll_result={
            "refresh_token": _REFRESH,
            "client_id": _DEVICE_CLIENT,
            "token_endpoint": _TOKEN_ENDPOINT,
            "access_token": _ACCESS,
        }
    )
    with _patch_device_login(login), patch("embry0.api.v1.credentials.emit_audit", new=AsyncMock()) as audit:
        started = await client.post("/api/v1/credentials/xai/device-login", json={"label": "grok-a"})
        assert started.status_code == 200, started.text
        r = await client.get(f"/api/v1/credentials/xai/device-login/{started.json()['login_id']}")
    assert r.status_code == 200, f"a finished device-login must create the credential, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["status"] == "done", "a finished device-login returns status done"
    credential = body["credential"]
    assert credential["provider"] == "xai" and credential["kind"] == "llm_oauth"
    assert credential["host"] == "api.x.ai"
    assert credential["label"] == "grok-a"
    assert credential["masked_preview"] == "refr••••••••alue"
    assert "secret" not in credential and "secret_ciphertext" not in credential
    leaked = _REFRESH + _ACCESS + _DEVICE_CODE
    assert _REFRESH not in r.text and _ACCESS not in r.text and _DEVICE_CODE not in r.text, (
        f"device-login response leaked a secret: {leaked}"
    )
    stored = app.state.credentials_repo.rows[credential["id"]]
    payload = json.loads(stored["secret_ciphertext"].removeprefix("enc:"))
    assert payload == {
        "refresh_token": _REFRESH,
        "client_id": _DEVICE_CLIENT,
        "token_endpoint": _TOKEN_ENDPOINT,
    }, "the stored secret is the refresh payload, not the access token"
    assert _ACCESS not in stored["secret_ciphertext"]
    bindings = await app.state.credentials_repo.bindings_for_scope("global", "")
    by_provider = {b["provider"]: b for b in bindings if b["role"] == "llm"}
    assert by_provider["anthropic"]["credential_id"] == anthropic_id, "adding xAI must keep the anthropic binding"
    assert by_provider["anthropic"]["priority"] == 1
    assert by_provider["xai"]["credential_id"] == credential["id"]
    assert by_provider["xai"]["priority"] == 0 and by_provider["xai"]["pinned"] is False
    assert _REFRESH in known_secret_values(), "the refresh token must be registered with the scrubber"
    assert _ACCESS not in known_secret_values()
    details = " ".join(str(call.kwargs.get("details")) for call in audit.await_args_list)
    assert _REFRESH not in details and _ACCESS not in details and _DEVICE_CODE not in details
    await _close_device_http(app)


async def test_xai_device_login_does_not_add_a_second_global_xai_binding(client, app):
    existing = await app.state.credentials_repo.create(
        kind="llm_oauth",
        provider="xai",
        host="api.x.ai",
        label="grok-old",
        secret_ciphertext="enc:old-refresh-token",
        masked_preview="old••••••••oken",
        created_by="test",
    )
    await app.state.credentials_repo.replace_bindings(
        "global",
        "",
        [{"credential_id": existing["id"], "role": "llm", "pinned": False, "priority": 0}],
        created_by="test",
    )
    login = _DeviceLogin(
        poll_result={
            "refresh_token": _REFRESH,
            "client_id": _DEVICE_CLIENT,
            "token_endpoint": _TOKEN_ENDPOINT,
        }
    )
    with _patch_device_login(login):
        started = await client.post("/api/v1/credentials/xai/device-login", json={"label": "grok-new"})
        assert started.status_code == 200, started.text
        r = await client.get(f"/api/v1/credentials/xai/device-login/{started.json()['login_id']}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"
    new_id = r.json()["credential"]["id"]
    assert new_id != existing["id"]
    bindings = await app.state.credentials_repo.bindings_for_scope("global", "")
    xai = [b for b in bindings if b["role"] == "llm" and b["provider"] == "xai"]
    assert [b["credential_id"] for b in xai] == [existing["id"]], "an existing global xAI llm binding is not duplicated"
    assert _REFRESH not in r.text
    await _close_device_http(app)


async def test_xai_device_login_expired_does_not_poll(client, app):
    login = _DeviceLogin(expires_in=0)
    with _patch_device_login(login):
        started = await client.post("/api/v1/credentials/xai/device-login", json={"label": "grok-a"})
        assert started.status_code == 200, started.text
        r = await client.get(f"/api/v1/credentials/xai/device-login/{started.json()['login_id']}")
    assert r.status_code == 200, f"an expired device-login returns status expired, got {r.status_code}: {r.text}"
    assert r.json() == {"status": "expired"}, "an expired device-login returns only status"
    assert login.polled is None, "an expired device-login must not poll"
    assert _DEVICE_CODE not in r.text
    assert app.state.credentials_repo.rows == {}
    await _close_device_http(app)


async def test_xai_device_login_unknown_is_404(client):
    r = await client.get("/api/v1/credentials/xai/device-login/missing-login")
    assert r.status_code == 404, f"an unknown device-login id is 404, got {r.status_code}: {r.text}"
    assert r.json()["detail"] == "device login not found", "an unknown device-login id names the missing login"


async def test_xai_device_login_terminal_reason_is_safe(client, app):
    from embry0.execution.xai_device_login import XaiDeviceLoginError

    login = _DeviceLogin(poll_error=XaiDeviceLoginError("rt-leaked"))
    with _patch_device_login(login):
        started = await client.post("/api/v1/credentials/xai/device-login", json={"label": "grok-a"})
        assert started.status_code == 200, started.text
        r = await client.get(f"/api/v1/credentials/xai/device-login/{started.json()['login_id']}")
    assert r.status_code == 400, f"an unsafe device-login reason becomes error, got {r.status_code}: {r.text}"
    assert r.json()["detail"] == "error", "only a known device-login reason word is shown"
    assert "rt-leaked" not in r.text and _DEVICE_CODE not in r.text
    assert app.state.credentials_repo.rows == {}
    await _close_device_http(app)


async def test_xai_device_login_known_terminal_reason_is_the_word(client, app):
    from embry0.execution.xai_device_login import XaiDeviceLoginError

    login = _DeviceLogin(poll_error=XaiDeviceLoginError("access_denied"))
    with _patch_device_login(login):
        started = await client.post("/api/v1/credentials/xai/device-login", json={"label": "grok-a"})
        assert started.status_code == 200, started.text
        r = await client.get(f"/api/v1/credentials/xai/device-login/{started.json()['login_id']}")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "access_denied", "a known device-login failure shows that reason word"
    assert _DEVICE_CODE not in r.text
    await _close_device_http(app)


async def test_xai_device_login_refuses_a_foreign_token_endpoint(client, app):
    login = _DeviceLogin(
        poll_result={
            "refresh_token": _REFRESH,
            "client_id": _DEVICE_CLIENT,
            "token_endpoint": "https://evil.example/token",
        }
    )
    with _patch_device_login(login):
        started = await client.post("/api/v1/credentials/xai/device-login", json={"label": "grok-a"})
        assert started.status_code == 200, started.text
        r = await client.get(f"/api/v1/credentials/xai/device-login/{started.json()['login_id']}")
    assert r.status_code == 400, f"a foreign token endpoint is rejected, got {r.status_code}: {r.text}"
    assert r.json()["detail"] == "endpoint_rejected"
    assert "evil.example" not in r.text and _REFRESH not in r.text
    assert app.state.credentials_repo.rows == {}, "a foreign token endpoint must not be stored"
    await _close_device_http(app)


# ---- #440: clearing a credential's git author ----


async def test_put_null_clears_git_author_and_omission_keeps_it(client, app):
    cid = (await _create(client)).json()["id"]

    r = await client.put(f"/api/v1/credentials/{cid}", json={"label": "alice-2"})
    assert r.status_code == 200, r.text
    assert (r.json()["git_author_name"], r.json()["git_author_email"]) == ("Alice", "alice@example.com"), (
        f"a PUT that omits the author fields changed them: {r.json()}"
    )

    r = await client.put(f"/api/v1/credentials/{cid}", json={"git_author_name": None, "git_author_email": "  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["git_author_name"], body["git_author_email"]) == (None, None), f"author not cleared: {body}"
    assert body["label"] == "alice-2"
