"""FastAPI dependency functions."""

from fastapi import Depends, Request

from legion.api.auth import verify_api_key

# Reusable dependency: requires a valid API key.
require_auth = Depends(verify_api_key)


def get_config(request: Request):
    """Return the app config from request state."""
    return request.app.state.config


def get_db(request: Request):
    """Return the DatabasePool from request state."""
    return request.app.state.db


def get_jobs_repo(request: Request):
    """Return the JobsRepository from request state."""
    return request.app.state.jobs_repo


def get_traces_repo(request: Request):
    """Return the TracesRepository from request state."""
    return request.app.state.traces_repo


def get_profiles_repo(request: Request):
    """Return the SandboxProfilesRepository from request state."""
    return request.app.state.profiles_repo


def get_budget_repo(request: Request):
    """Return the BudgetConfigRepository from request state."""
    return request.app.state.budget_repo


def get_context_repo(request: Request):
    """Return the ContextConfigRepository from request state."""
    return request.app.state.context_repo


def get_workflow_registry(request: Request):
    """Return the WorkflowRegistry from request state."""
    return request.app.state.workflow_registry
