"""Shared inbound-webhook plumbing: verification and payload parsing (#159).

Three forges, three different ways of proving a delivery is genuine:

===========  ==========================  ==================================
Forge        Header                      Proof
===========  ==========================  ==================================
GitHub       ``X-Hub-Signature-256``     HMAC-SHA256, ``sha256=`` prefixed
Linear       ``Linear-Signature``        HMAC-SHA256, bare hex, no prefix
GitLab       ``X-Gitlab-Token``          the shared secret in plaintext
===========  ==========================  ==================================

Only the last step differs. The surrounding policy — what an unset secret
means, what a missing header means, how ``WEBHOOK_DEV_MODE`` interacts with
both — is identical and was previously written twice, once in
``auth.verify_webhook_signature`` and once inline in the Linear route. A third
copy for GitLab is the thing this module exists to prevent.

``WEBHOOK_DEV_MODE`` bypasses verification **only when the secret is empty**.
A configured secret is always enforced, dev mode or not; that rule is load
bearing and pinned by tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json as json_module
from enum import StrEnum
from typing import Any

import structlog
from fastapi import HTTPException

logger = structlog.get_logger(__name__)


class SignatureScheme(StrEnum):
    """How a forge proves a delivery came from it."""

    HMAC_SHA256_PREFIXED = "hmac_sha256_prefixed"  # GitHub
    HMAC_SHA256_BARE = "hmac_sha256_bare"  # Linear
    SHARED_SECRET = "shared_secret"  # GitLab


def _expected_signature(scheme: SignatureScheme, body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if scheme is SignatureScheme.HMAC_SHA256_PREFIXED:
        return f"sha256={digest}"
    if scheme is SignatureScheme.HMAC_SHA256_BARE:
        return digest
    return secret


def verify_webhook(
    *,
    scheme: SignatureScheme,
    body: bytes,
    presented: str,
    secret: str,
    webhook_dev_mode: bool = False,
    forge: str = "webhook",
) -> None:
    """Authenticate one delivery, or raise.

    Raises 503 when no secret is configured outside dev mode (a webhook that
    silently accepts anything is worse than one that refuses to run), 401 when
    the header is missing or does not match.
    """
    if not secret:
        if webhook_dev_mode:
            return
        raise HTTPException(status_code=503, detail=f"{forge} webhook secret is not configured")
    if not presented:
        raise HTTPException(status_code=401, detail=f"Missing {forge} webhook signature")

    # compare_digest for every scheme, including the plaintext one — GitLab's
    # token is still a secret and a naive == leaks its length by timing.
    if not hmac.compare_digest(presented, _expected_signature(scheme, body, secret)):
        raise HTTPException(status_code=401, detail=f"Invalid {forge} webhook signature")


def unwrap_smee_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a smee.io relay envelope. No-op for direct deliveries.

    smee re-serialises the real payload into a ``payload`` string field. The
    unwrap is deliberately **single-pass**: a genuine forge payload could
    legitimately contain a string field called ``payload``, and recursing
    would corrupt it. There is a regression test pinning exactly this.
    """
    inner = payload.get("payload")
    if isinstance(inner, str):
        try:
            decoded = json_module.loads(inner)
        except (ValueError, RecursionError):  # see embry0.json_text
            return payload
        if isinstance(decoded, dict):
            return decoded
    return payload


def parse_webhook_body(body: bytes) -> dict[str, Any]:
    """Decode a delivery body to a dict, unwrapping a smee envelope."""
    try:
        payload = json_module.loads(body)
    except (ValueError, RecursionError):  # see embry0.json_text
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    return unwrap_smee_envelope(payload)
