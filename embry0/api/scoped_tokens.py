"""Short-lived HMAC tokens scoped to a single resource (RAV-1060/RAV-1070).

Browser transports that cannot carry an Authorization header — EventSource
(SSE query param) and WebSocket (subprotocol) — must never carry a long-lived
API key where it can be logged (URLs) or replayed indefinitely. Instead the
client trades its Bearer credential for a token scoped to ONE resource
(``sse:<run_id>``, ``ws:<job_id>``) with a minutes-scale TTL, and presents
that on the stream.

Token format: ``v1.<expires_at>.<hmac_sha256_hex>`` where the signature covers
``<scope>.<expires_at>`` under a secret derived from the deploy's signing
material. Deriving (rather than a random per-boot secret) keeps tokens valid
across orchestrator restarts within their TTL, and means rotating the
underlying secret also invalidates all outstanding tokens — exactly the
semantics rotation should have.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

_TOKEN_VERSION = "v1"

# Long enough to cover a stream's whole session (clients mint one token per
# stream open), short enough that a leaked log line goes stale in minutes.
DEFAULT_TTL_SECONDS = 900


def signing_material(config: Any) -> str:
    """The secret string scoped tokens derive from (RAV-1056 Phase B).

    Prefers SESSION_SECRET (SSO deploys) so the derivation no longer couples
    to the master API key — a prerequisite for multi-key auth, where "the"
    API key stops being a meaningful singleton. Falls back to the master key
    on LAN-only deploys without SSO, preserving rotation semantics there.
    """
    return str(getattr(config, "session_secret", "") or config.api_key or "")


def _token_secret(material: str) -> bytes:
    return hashlib.sha256(b"embry0-scoped-token-" + _TOKEN_VERSION.encode() + b"-" + material.encode()).digest()


def _sign(material: str, scope: str, expires_at: int) -> str:
    sig = hmac.new(_token_secret(material), f"{scope}.{expires_at}".encode(), hashlib.sha256).hexdigest()
    return f"{_TOKEN_VERSION}.{expires_at}.{sig}"


def mint_scoped_token(
    material: str,
    scope: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> tuple[str, int]:
    """Mint a token for *scope*. Returns ``(token, expires_in_seconds)``."""
    expires_at = int(now if now is not None else time.time()) + ttl_seconds
    return _sign(material, scope, expires_at), ttl_seconds


#: Prefixes the scope of a token minted for a stream its minter may not read. Such a token opens the stream, which
#: then streams nothing, as the stream of a resource that does not exist; the two answers are the same, so the token
#: never tells whether the resource exists. No resource scope starts with it, so it never opens one that reads.
_READS_NOTHING = "reads-nothing:"


def reads_nothing(scope: str) -> str:
    """The scope a token for ``scope``'s stream is signed for when its minter may not read the resource."""
    return _READS_NOTHING + scope


def token_reads(material: str, scope: str, token: str, *, now: float | None = None) -> bool | None:
    """What ``token`` opens on ``scope``'s stream: True, the stream; False, a stream that reads nothing (a token
    minted for :func:`reads_nothing`); None, nothing: the token is not valid for ``scope``."""
    if verify_scoped_token(material, scope, token, now=now):
        return True
    if verify_scoped_token(material, reads_nothing(scope), token, now=now):
        return False
    return None


def verify_scoped_token(material: str, scope: str, token: str, *, now: float | None = None) -> bool:
    """Constant-time verify of a scoped token: version, expiry, signature.

    Empty material never verifies (#518): the key would then be derived from
    public constants alone, so anyone could mint a token that passes.
    """
    if not material:
        return False
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _TOKEN_VERSION:
        return False
    try:
        expires_at = int(parts[1])
    except ValueError:
        return False
    if (now if now is not None else time.time()) >= expires_at:
        return False
    expected = _sign(material, scope, expires_at)
    return hmac.compare_digest(token, expected)
