"""One refresher per registered xAI oauth account. The file store is not this path."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from embry0.safety.error_codes import ErrorCode
from embry0.safety.redaction import known_secret_values, register_known_secrets

_TOKEN_URL = "https://auth.x.ai/oauth2/token"


def _import_registry():
    try:
        from embry0.execution.xai_credential import XaiRefresherRegistry
    except ImportError as exc:
        pytest.fail(f"XaiRefresherRegistry must be importable: {exc}")
    return XaiRefresherRegistry


class _Secrets:
    async def encrypt(self, plaintext: str) -> str:
        return "enc:" + plaintext

    async def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith("enc:"):
            raise ValueError("not ciphertext")
        return ciphertext[4:]


class _Repo:
    def __init__(self, rows: list[dict], secrets: dict[str, str]) -> None:
        self.rows = rows
        self.secrets = secrets
        self.gets: list[str] = []
        self.rotations: list[tuple[str, str, str]] = []

    async def list_all(self) -> list[dict]:
        return [dict(row) for row in self.rows]

    async def get(self, credential_id: str) -> dict | None:
        self.gets.append(credential_id)
        row = next((item for item in self.rows if item["id"] == credential_id), None)
        if row is None or credential_id not in self.secrets:
            return None
        return {**row, "secret_ciphertext": self.secrets[credential_id]}

    async def rotate(self, credential_id: str, *, secret_ciphertext: str, masked_preview: str) -> dict:
        self.rotations.append((credential_id, secret_ciphertext, masked_preview))
        self.secrets[credential_id] = secret_ciphertext
        return {"id": credential_id}


class _Health:
    def __init__(self) -> None:
        self.reports: list[tuple[str, str]] = []

    async def report(self, credential_id: str, code: str) -> dict:
        self.reports.append((credential_id, code))
        return {"id": credential_id, "status": "quarantined"}


def _row(credential_id: str, *, status: str = "ok", kind: str = "llm_oauth", provider: str = "xai", until=None) -> dict:
    return {
        "id": credential_id,
        "kind": kind,
        "provider": provider,
        "status": status,
        "status_reason": None,
        "quarantined_until": until,
        "label": credential_id,
    }


def _cipher(refresh_token: str) -> str:
    payload = {
        "refresh_token": refresh_token,
        "client_id": "client-abc",
        "token_endpoint": _TOKEN_URL,
    }
    return "enc:" + json.dumps(payload)


@pytest.fixture
def refuse_auth_json(monkeypatch):
    original = Path.read_text

    def guarded(self, *args, **kwargs):
        if self.name == "auth.json":
            raise AssertionError("registry must not read auth.json")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)


@pytest.fixture
def scrubber_snapshot():
    before = known_secret_values()
    yield
    register_known_secrets(before, reset=True)


def _client(
    responses: dict[str, httpx.Response], *, raise_for: set[str] | None = None
) -> tuple[httpx.AsyncClient, list[str]]:
    seen: list[str] = []
    raise_for = raise_for or set()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == _TOKEN_URL, f"refresh must post to the xAI token endpoint, got {request.url}"
        form = parse_qs(request.content.decode())
        refresh = form["refresh_token"][0]
        seen.append(refresh)
        if refresh in raise_for:
            raise httpx.ConnectError("connection refused")
        return responses[refresh]

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def _ok(access_token: str, refresh_token: str, *, expires_in: int = 3600) -> httpx.Response:
    return httpx.Response(
        200,
        json={"access_token": access_token, "refresh_token": refresh_token, "expires_in": expires_in},
    )


async def test_refresh_all_returns_every_usable_xai_oauth_row(refuse_auth_json, scrubber_snapshot):
    """Disabled and still-quarantined rows are skipped. A past window is usable."""
    XaiRefresherRegistry = _import_registry()
    future = datetime.now(UTC) + timedelta(hours=2)
    past = datetime.now(UTC) - timedelta(hours=2)
    rows = [
        _row("cred-a"),
        _row("cred-b"),
        _row("cred-past", until=past, status="quarantined"),
        _row("cred-disabled", status="disabled"),
        _row("cred-held", status="quarantined"),
        _row("cred-future", status="quarantined", until=future),
        _row("cred-anthropic", provider="anthropic"),
        _row("cred-api", kind="llm_api_key"),
    ]
    secrets = {
        "cred-a": _cipher("rt-a"),
        "cred-b": _cipher("rt-b"),
        "cred-past": _cipher("rt-past"),
        "cred-disabled": _cipher("rt-disabled"),
        "cred-held": _cipher("rt-held"),
        "cred-future": _cipher("rt-future"),
    }
    repo = _Repo(rows, secrets)
    health = _Health()
    client, seen = _client(
        {
            "rt-a": _ok("access-token-cred-a-ok", "rt-a"),
            "rt-b": _ok("access-token-cred-b-ok", "rt-b"),
            "rt-past": _ok("access-token-cred-past-ok", "rt-past"),
        }
    )
    registry = XaiRefresherRegistry(repo, _Secrets(), http_client=client, health=health)

    refreshed = await registry.refresh_all()

    assert set(refreshed) == {"cred-a", "cred-b", "cred-past"}, (
        f"only usable xAI oauth rows refresh, got {sorted(refreshed)}"
    )
    assert refreshed["cred-a"][0] == "access-token-cred-a-ok"
    assert refreshed["cred-b"][0] == "access-token-cred-b-ok"
    assert "access-token-cred-a-ok" in known_secret_values(), "minted access token must be scrubbed"
    assert "rt-a" not in known_secret_values(), "refresh token must not be registered as a fresh access token"
    expiry = datetime.fromisoformat(refreshed["cred-a"][1])
    assert expiry.tzinfo is not None
    remaining = expiry - datetime.now(UTC)
    assert timedelta(minutes=30) < remaining < timedelta(hours=2), refreshed["cred-a"][1]
    assert set(repo.gets) == {"cred-a", "cred-b", "cred-past"}, (
        f"ciphertext is loaded only for usable rows, got {repo.gets}"
    )
    assert repo.rotations == [], "an unchanged refresh token is not written back"
    assert health.reports == []
    assert set(seen) == {"rt-a", "rt-b", "rt-past"}
    refresher = registry.get("cred-a")
    assert refresher is not None
    assert refresher._grok_cli_store is None, "a registry refresher must not re-seed from the CLI store"
    await client.aclose()


async def test_disabled_between_refreshes_is_not_served_from_cache(refuse_auth_json, scrubber_snapshot):
    XaiRefresherRegistry = _import_registry()
    rows = [_row("cred-a")]
    repo = _Repo(rows, {"cred-a": _cipher("rt-a")})
    client, seen = _client({"rt-a": _ok("access-token-cred-a-ok", "rt-a")})
    registry = XaiRefresherRegistry(repo, _Secrets(), http_client=client, health=_Health())

    first = await registry.refresh_all()
    assert "cred-a" in first
    rows[0]["status"] = "disabled"
    second = await registry.refresh_all()

    assert second == {}, f"a row disabled after the first refresh must not be served, got {second}"
    assert seen == ["rt-a"], f"the cached access token must not hide a disabled row, posts={seen}"
    await client.aclose()


async def test_terminal_refresh_quarantines_that_row_only(refuse_auth_json, scrubber_snapshot):
    """A 4xx kills that lineage. The other account is still returned. The proxy stays up."""
    XaiRefresherRegistry = _import_registry()
    rows = [_row("cred-dead"), _row("cred-ok")]
    repo = _Repo(rows, {"cred-dead": _cipher("rt-dead"), "cred-ok": _cipher("rt-ok")})
    health = _Health()
    client, _seen = _client(
        {
            "rt-dead": httpx.Response(401, json={"error": "invalid_grant"}),
            "rt-ok": _ok("access-token-cred-ok-live", "rt-ok"),
        }
    )
    registry = XaiRefresherRegistry(repo, _Secrets(), http_client=client, health=health)

    refreshed = await registry.refresh_all()

    assert set(refreshed) == {"cred-ok"}, f"a dead lineage must not drop the other account, got {set(refreshed)}"
    assert health.reports == [("cred-dead", ErrorCode.AUTH_REJECTED)], health.reports
    assert registry.get("cred-dead") is not None
    assert registry.get("cred-dead")._grok_cli_store is None
    await client.aclose()


async def test_transport_error_skips_the_row_without_quarantine(refuse_auth_json, scrubber_snapshot):
    XaiRefresherRegistry = _import_registry()
    rows = [_row("cred-down"), _row("cred-ok")]
    repo = _Repo(rows, {"cred-down": _cipher("rt-down"), "cred-ok": _cipher("rt-ok")})
    health = _Health()
    client, _seen = _client({"rt-ok": _ok("access-token-cred-ok-live", "rt-ok")}, raise_for={"rt-down"})
    registry = XaiRefresherRegistry(repo, _Secrets(), http_client=client, health=health)

    refreshed = await registry.refresh_all()

    assert set(refreshed) == {"cred-ok"}
    assert health.reports == [], f"a transport failure is not a dead lineage, got {health.reports}"
    await client.aclose()


async def test_refresh_credential_leaves_the_other_account_alone(refuse_auth_json, scrubber_snapshot):
    XaiRefresherRegistry = _import_registry()
    repo = _Repo(
        [_row("cred-a"), _row("cred-b")],
        {"cred-a": _cipher("rt-a"), "cred-b": _cipher("rt-b")},
    )
    client, seen = _client(
        {
            "rt-a": _ok("access-token-cred-a-only", "rt-a"),
            "rt-b": _ok("access-token-cred-b-only", "rt-b"),
        }
    )
    registry = XaiRefresherRegistry(repo, _Secrets(), http_client=client, health=_Health())

    token, _expiry, _seq = await registry.refresh_credential("cred-a")

    assert token == "access-token-cred-a-only"
    assert seen == ["rt-a"], f"verifying one account must not refresh the other, posts={seen}"
    assert set(repo.gets) == {"cred-a"}
    await client.aclose()


async def test_rotated_refresh_token_is_saved_encrypted(refuse_auth_json, scrubber_snapshot):
    import structlog

    XaiRefresherRegistry = _import_registry()
    repo = _Repo([_row("cred-a")], {"cred-a": _cipher("rt-old")})
    client, _seen = _client({"rt-old": _ok("access-token-cred-a-new", "rt-rotated-value")})
    registry = XaiRefresherRegistry(repo, _Secrets(), http_client=client, health=_Health())

    with structlog.testing.capture_logs() as logs:
        refreshed = await registry.refresh_all()

    assert refreshed["cred-a"][0] == "access-token-cred-a-new"
    assert len(repo.rotations) == 1, "a rotated refresh token has to be written back"
    credential_id, ciphertext, preview = repo.rotations[0]
    assert credential_id == "cred-a"
    assert ciphertext.startswith("enc:")
    saved = json.loads(ciphertext.removeprefix("enc:"))
    assert saved["refresh_token"] == "rt-rotated-value"
    assert "access-token-cred-a-new" not in ciphertext
    assert preview != "rt-rotated-value"
    rendered = json.dumps(logs)
    assert "rt-rotated-value" not in rendered
    assert "access-token-cred-a-new" not in rendered
    assert "rt-old" not in rendered
    await client.aclose()


async def test_a_registry_row_is_never_reseeded_from_the_cli_store(tmp_path, refuse_auth_json):
    """The registry builds each refresher without a CLI store. Should one be given a CLI store
    anyway, a dead registry lineage must still not fall through to the operator's grant."""
    from embry0.execution.xai_credential import RegistryXaiStore, XaiCredentialError, XaiTokenRefresher

    repo = _Repo([_row("cred-dead")], {"cred-dead": _cipher("rt-dead")})
    client, _seen = _client({"rt-dead": httpx.Response(401, json={"error": "invalid_grant"})})
    refresher = XaiTokenRefresher(
        RegistryXaiStore(repo, _Secrets(), "cred-dead"),
        http_client=client,
        grok_cli_store=tmp_path / "auth.json",
    )

    with pytest.raises(XaiCredentialError, match="never re-seeded from the Grok CLI store"):
        await refresher.ensure_fresh()
    assert repo.rotations == [], f"a refused re-seed must not write the row, got {repo.rotations}"
    await client.aclose()


async def test_a_token_is_numbered_when_it_is_fetched_not_when_its_batch_returns(refuse_auth_json, scrubber_snapshot):
    """refresh_all fetches one account, then the next. A forced refresh of the first, done while the batch is still on
    the second, fetched a newer token: it carries the newer number, so its push is never undone by the batch's."""
    import asyncio

    XaiRefresherRegistry = _import_registry()
    repo = _Repo([_row("cred-a"), _row("cred-b")], {"cred-a": _cipher("rt-a"), "cred-b": _cipher("rt-b")})
    registry = XaiRefresherRegistry(repo, _Secrets(), health=_Health())
    gate = asyncio.Event()

    class _Refresher:
        def __init__(self, tokens: list[str], wait: bool = False) -> None:
            self.tokens, self.wait = tokens, wait
            self._access_expiry, self._clock = 3600.0, lambda: 0.0

        async def ensure_fresh(self, *, force: bool = False) -> str:
            if self.wait:
                await gate.wait()
            return self.tokens.pop(0)

    registry._refreshers["cred-a"] = _Refresher(["at-a-1", "at-a-2"])
    registry._refreshers["cred-b"] = _Refresher(["at-b-1"], wait=True)
    batch = asyncio.create_task(registry.refresh_all())
    await asyncio.sleep(0.01)  # the batch has fetched cred-a and waits on cred-b
    forced = await registry.refresh_credential("cred-a", force=True)
    gate.set()
    refreshed = await batch
    assert (refreshed["cred-a"].token, forced.token) == ("at-a-1", "at-a-2")
    assert refreshed["cred-a"].seq < forced.seq, (
        f"the token fetched first carries the newer number: {refreshed['cred-a'].seq} vs {forced.seq}"
    )
