"""API authentication helpers."""

from fastapi import HTTPException, Request, status


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
