"""FastAPI dependency functions."""

from fastapi import Depends, Request

from legion.api.auth import verify_api_key

# Reusable dependency: requires a valid API key.
require_auth = Depends(verify_api_key)


def get_config(request: Request):
    """Return the app config from request state."""
    return request.app.state.config


def get_jobs_repo(request: Request):
    """Return the JobsRepository from request state."""
    return request.app.state.jobs_repo
