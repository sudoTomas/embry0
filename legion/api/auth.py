"""API authentication helpers."""

import hashlib
import hmac

from fastapi import HTTPException, Request, status


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> None:
    """Verify a GitHub webhook HMAC-SHA256 signature.

    Raises HTTPException 401 if the signature is missing or invalid.
    """
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")


async def verify_api_key(request: Request) -> str:
    """Verify the API key from the request headers.

    Returns the API key if valid. Raises HTTPException if invalid.
    """
    config = request.app.state.config

    # In dev mode, skip authentication.
    if config.dev_mode:
        return "dev"

    api_key = request.headers.get("X-API-Key") or request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    if api_key != config.api_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")

    return api_key
