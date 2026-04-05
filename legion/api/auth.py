"""API authentication helpers."""

import hashlib
import hmac

from fastapi import HTTPException


def verify_api_key(api_key: str, authorization: str, dev_mode: bool) -> None:
    """Verify the API key from explicit parameters.

    Raises HTTPException 401 if the key is missing or invalid.
    Skips verification when dev_mode is True or no key is configured.
    """
    if dev_mode or not api_key:
        return
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    if not hmac.compare_digest(token, api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> None:
    """Verify a GitHub webhook HMAC-SHA256 signature.

    Raises HTTPException 401 if the signature is missing or invalid.
    """
    if not signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    expected = "sha256=" + hmac.HMAC(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
