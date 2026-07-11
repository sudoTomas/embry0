"""API authentication helpers."""

import hashlib
import hmac

from fastapi import HTTPException


def verify_api_key(api_key: str, authorization: str, auth_dev_mode: bool) -> None:
    """Verify the API key from explicit parameters.

    Raises HTTPException 401 if the key is missing or invalid.
    Skips verification when auth_dev_mode is True or no key is configured.
    """
    if auth_dev_mode or not api_key:
        return
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    if not hmac.compare_digest(token, api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


def verify_webhook_signature(body: bytes, signature: str, secret: str, webhook_dev_mode: bool = False) -> None:
    """Verify a GitHub webhook HMAC-SHA256 signature.

    - If ``secret`` is set: always require and verify a valid signature (401 on mismatch).
    - If ``secret`` is empty and ``webhook_dev_mode`` is True: skip verification (smee.io local relay flow).
    - If ``secret`` is empty and ``webhook_dev_mode`` is False: raise 503 (not configured for production).
    """
    if not secret:
        if webhook_dev_mode:
            return
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    expected = "sha256=" + hmac.HMAC(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
