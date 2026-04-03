"""FastAPI dependency functions."""

from fastapi import Depends

from legion.api.auth import verify_api_key

# Reusable dependency: requires a valid API key.
require_auth = Depends(verify_api_key)
