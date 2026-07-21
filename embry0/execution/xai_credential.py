"""SuperGrok OAuth credential store + token refresher for the direct-xAI executor (EMB-45).

The SuperGrok subscription bearer drives grok-4.5 on ``api.x.ai``. Its access token
lives ~6h and its **refresh token rotates on every refresh** — so exactly one process
must own the refresh lineage, or concurrent refreshes invalidate each other.

``XaiTokenRefresher`` is that single owner (orchestrator-side). It:

- holds the durable ``{refresh_token, client_id, token_endpoint}`` in a Fernet-encrypted
  file (keyed by ``ENVIRONMENT_SECRET_KEY``);
- seeds it once from the Grok CLI store (``~/.grok/auth.json``) when the durable file
  does not yet exist;
- exchanges ``refresh_token`` → ``access_token`` at ``auth.x.ai/oauth2/token``
  (``grant_type=refresh_token``, public client, no secret);
- **persists the rotated refresh_token back before serving**, under an ``asyncio.Lock`` so
  concurrent callers serialize (never double-spend a rotating refresh token);
- returns a valid access token via :meth:`XaiTokenRefresher.ensure_fresh`, refreshing when
  the cached token is within :data:`REFRESH_MARGIN_SECONDS` of expiry.

The access token is short-lived and never persisted; only the rotating refresh token is
durable. The token never enters a sandbox — the ``xai-proxy`` injects it at egress
(see :mod:`embry0.execution.proxy.xai_proxy`).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import httpx
import structlog
from cryptography.fernet import InvalidToken

from embry0.storage.encryption import FernetSecretsProvider

logger = structlog.get_logger(__name__)

# Refresh when the cached access token has less than this many seconds of life left,
# so a request never rides a token that expires mid-flight.
REFRESH_MARGIN_SECONDS = 30 * 60
# If the token endpoint omits expires_in, assume a conservative lifetime.
_DEFAULT_EXPIRES_IN = 6 * 3600
# Never trust an absurdly short lifetime — clamp so a bad expires_in can't spin the loop.
_MIN_EXPIRES_IN = 5 * 60

DEFAULT_GROK_CLI_STORE = Path.home() / ".grok" / "auth.json"


class XaiCredentialError(RuntimeError):
    """Raised when the xAI OAuth credential cannot be loaded, seeded, or refreshed."""


@dataclass(frozen=True, slots=True)
class DurableState:
    """The persisted, rotating credential lineage. The access token is NOT here."""

    refresh_token: str
    client_id: str
    token_endpoint: str


class _AsyncSecrets(Protocol):
    async def encrypt(self, plaintext: str) -> str: ...
    async def decrypt(self, ciphertext: str) -> str: ...


class XaiCredentialStore:
    """Durable, Fernet-encrypted store for the rotating refresh-token lineage.

    The on-disk file is a single Fernet token wrapping the JSON of :class:`DurableState`.
    Writes are atomic (tmp-file + ``os.replace``) so a crash mid-write cannot corrupt the
    lineage — a torn write would only ever lose the *new* refresh token, never leave an
    unreadable file.
    """

    def __init__(self, path: str | Path, secrets: _AsyncSecrets) -> None:
        self._path = Path(path)
        self._secrets = secrets

    @property
    def path(self) -> Path:
        return self._path

    async def load(self) -> DurableState | None:
        """Return the persisted state, or None if the durable file does not exist."""
        if not self._path.exists():
            return None
        try:
            ciphertext = self._path.read_text(encoding="utf-8")
            plaintext = await self._secrets.decrypt(ciphertext)
            data = json.loads(plaintext)
            return DurableState(
                refresh_token=data["refresh_token"],
                client_id=data["client_id"],
                token_endpoint=data["token_endpoint"],
            )
        except (OSError, KeyError, ValueError, InvalidToken) as exc:
            raise XaiCredentialError(f"xAI credential store at {self._path} is unreadable or corrupt: {exc}") from exc

    async def save(self, state: DurableState) -> None:
        """Atomically persist *state* (encrypted). Creates parent dirs as needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "refresh_token": state.refresh_token,
                "client_id": state.client_id,
                "token_endpoint": state.token_endpoint,
            }
        )
        ciphertext = await self._secrets.encrypt(payload)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(ciphertext, encoding="utf-8")
        os.replace(tmp, self._path)

    async def seed_from_grok_cli(self, cli_store_path: str | Path) -> DurableState:
        """Seed the durable store once from the Grok CLI's ``~/.grok/auth.json``.

        The CLI store is a dict keyed by ``<issuer>::<client>`` whose value carries
        ``refresh_token``, ``oidc_client_id``, and ``oidc_issuer``. The token endpoint is
        ``<issuer>/oauth2/token``. After seeding, embry0 owns its own rotating copy and
        never reads the CLI store again (avoids a two-owner rotation conflict).
        """
        cli_path = Path(cli_store_path)
        try:
            raw = json.loads(cli_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise XaiCredentialError(f"cannot read Grok CLI store at {cli_path} to seed xAI credential: {exc}") from exc
        if not isinstance(raw, dict) or not raw:
            raise XaiCredentialError(f"Grok CLI store at {cli_path} is empty or malformed")
        # Single-entry store keyed by "<issuer>::<client>"; take the first entry.
        entry = next(iter(raw.values()))
        if not isinstance(entry, dict):
            raise XaiCredentialError(f"Grok CLI store entry at {cli_path} is malformed")
        refresh_token = entry.get("refresh_token", "")
        client_id = entry.get("oidc_client_id", "")
        issuer = entry.get("oidc_issuer", "")
        if not refresh_token or not client_id or not issuer:
            raise XaiCredentialError(
                f"Grok CLI store at {cli_path} is missing refresh_token/oidc_client_id/oidc_issuer"
            )
        state = DurableState(
            refresh_token=refresh_token,
            client_id=client_id,
            token_endpoint=f"{issuer.rstrip('/')}/oauth2/token",
        )
        await self.save(state)
        logger.info("xai_credential_seeded_from_grok_cli", source=str(cli_path))
        return state


class XaiTokenRefresher:
    """Single owner of the rotating SuperGrok refresh token.

    ``ensure_fresh()`` returns a currently-valid access token, refreshing (and persisting
    the rotated refresh token) when the cached token is near expiry. All refreshes are
    serialized under an ``asyncio.Lock`` so a rotating refresh token is never double-spent.
    """

    def __init__(
        self,
        store: XaiCredentialStore,
        *,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._http = http_client
        self._owns_http = http_client is None
        self._clock = clock
        self._lock = asyncio.Lock()
        self._access_token = ""
        self._access_expiry = 0.0  # in the clock's units

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def ensure_fresh(self) -> str:
        """Return a currently-valid access token, refreshing if within the margin of expiry."""
        async with self._lock:
            now = self._clock()
            if self._access_token and now < (self._access_expiry - REFRESH_MARGIN_SECONDS):
                return self._access_token
            await self._refresh_locked()
            return self._access_token

    async def _refresh_locked(self) -> None:
        state = await self._store.load()
        if state is None:
            raise XaiCredentialError(f"xAI credential store at {self._store.path} is empty; seed it before refreshing")
        try:
            resp = await self._client().post(
                state.token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": state.refresh_token,
                    "client_id": state.client_id,
                },
            )
        except httpx.HTTPError as exc:
            raise XaiCredentialError(f"xAI token refresh request failed: {exc}") from exc
        if resp.status_code != 200:
            # Redact the body — it may echo the refresh token.
            raise XaiCredentialError(f"xAI token refresh returned HTTP {resp.status_code} from {state.token_endpoint}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise XaiCredentialError("xAI token refresh returned a non-JSON body") from exc

        access_token = payload.get("access_token", "")
        if not access_token:
            raise XaiCredentialError("xAI token refresh response had no access_token")

        # The refresh token rotates on use: persist the NEW one before doing anything else,
        # so a crash after rotation never strands us with the now-invalidated old token.
        new_refresh = payload.get("refresh_token") or state.refresh_token
        if new_refresh != state.refresh_token:
            await self._store.save(replace(state, refresh_token=new_refresh))
            logger.info("xai_refresh_token_rotated")

        expires_in = payload.get("expires_in")
        try:
            ttl = int(expires_in) if expires_in is not None else _DEFAULT_EXPIRES_IN
        except (TypeError, ValueError):
            ttl = _DEFAULT_EXPIRES_IN
        ttl = max(ttl, _MIN_EXPIRES_IN)

        self._access_token = access_token
        self._access_expiry = self._clock() + ttl
        logger.info("xai_access_token_refreshed", ttl_seconds=ttl)


async def build_refresher(
    *,
    store_path: str | Path,
    secret_key: str,
    grok_cli_store: str | Path = DEFAULT_GROK_CLI_STORE,
    http_client: httpx.AsyncClient | None = None,
) -> XaiTokenRefresher:
    """Construct a refresher, seeding the durable store from the Grok CLI on first run.

    Raises :class:`XaiCredentialError` if no durable store exists and the Grok CLI store
    cannot be read — the orchestrator must not silently start the direct-xAI path without a
    working credential.
    """
    if not secret_key:
        raise XaiCredentialError("ENVIRONMENT_SECRET_KEY is required to encrypt the xAI credential store")
    store = XaiCredentialStore(store_path, FernetSecretsProvider(secret_key=secret_key))
    if await store.load() is None:
        await store.seed_from_grok_cli(grok_cli_store)
    return XaiTokenRefresher(store, http_client=http_client)
