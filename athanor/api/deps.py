"""FastAPI dependency functions."""

from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from athanor.api.auth import verify_api_key, verify_dashboard_jwt


def _auth_dependency(
    request: Request,
    authorization: str = Header(default=""),
) -> None:
    """FastAPI dependency that extracts auth parameters and delegates to verify_api_key."""
    config = request.app.state.config
    verify_api_key(
        api_key=config.api_key or "",
        authorization=authorization,
        auth_dev_mode=config.auth_dev_mode,
    )


# Reusable dependency: requires a valid API key.
require_auth = Depends(_auth_dependency)


def get_config(request: Request) -> Any:
    """Return the app config from request state."""
    return request.app.state.config


def get_db(request: Request) -> Any:
    """Return the DatabasePool from request state."""
    return request.app.state.db


def get_jobs_repo(request: Request) -> Any:
    """Return the JobsRepository from request state."""
    return request.app.state.jobs_repo


def get_traces_repo(request: Request) -> Any:
    """Return the TracesRepository from request state."""
    return request.app.state.traces_repo


def get_profiles_repo(request: Request) -> Any:
    """Return the SandboxProfilesRepository from request state."""
    return request.app.state.profiles_repo


def get_budget_repo(request: Request) -> Any:
    """Return the BudgetConfigRepository from request state."""
    return request.app.state.budget_repo


def get_context_repo(request: Request) -> Any:
    """Return the ContextConfigRepository from request state."""
    return request.app.state.context_repo


def get_workflow_registry(request: Request) -> Any:
    """Return the WorkflowRegistry from request state."""
    return request.app.state.workflow_registry


def get_agent_defs_repo(request: Request) -> Any:
    """Return the AgentDefinitionsRepository from request state."""
    return request.app.state.agent_defs_repo


def get_templates_repo(request: Request) -> Any:
    """Return the PipelineTemplatesRepository from request state."""
    return request.app.state.templates_repo


def get_integration_repo(request: Request) -> Any:
    """Return the IntegrationConfigRepository from request state."""
    return request.app.state.integration_repo


def get_provider_repo(request: Request) -> Any:
    """Return the ProviderConfigRepository from request state."""
    return request.app.state.provider_repo


def get_issues_repo(request: Request) -> Any:
    """Return the IssuesRepository from request state."""
    return request.app.state.issues_repo


def get_github_sync(request: Request) -> Any:
    """Return the GitHubSyncService from request state."""
    return request.app.state.github_sync


def get_issue_executor(request: Request) -> Any:
    """Return the IssueExecutor from request state."""
    return request.app.state.issue_executor


def get_inputs_repo(request: Request) -> Any:
    """Return the IssueInputsRepository from request state."""
    return request.app.state.inputs_repo


def get_qa_minio(request: Request) -> Any:
    """Return the QA MinIO client from request state, or 503 if unconfigured."""
    from fastapi import HTTPException

    minio = getattr(request.app.state, "qa_minio", None)
    if minio is None:
        raise HTTPException(status_code=503, detail="QA artifact storage unavailable")
    return minio


def get_docker(request: Request) -> Any:
    """Return the Docker client from request state, or 503 if unconfigured."""
    from fastapi import HTTPException

    docker = getattr(request.app.state, "docker", None)
    if docker is None:
        raise HTTPException(status_code=503, detail="docker client unavailable")
    return docker


# ── Phase 4: dashboard auth dependency ──

def _dashboard_auth_dependency(
    request: Request,
    authorization: str = Header(default=""),
) -> None:
    """Require either a Bearer API key OR a dashboard_session cookie.

    Use cases:
      - curl: `Authorization: Bearer <ATHANOR_API_KEY>` — same as v1.
      - browser: HttpOnly cookie `dashboard_session=<jwt>` — set by /v2/auth/dashboard/login.

    Skips both checks when auth_dev_mode is True (matches the existing v1 behavior).
    """
    config = request.app.state.config
    if getattr(config, "auth_dev_mode", False):
        return

    api_key = config.api_key or ""

    # Try Bearer first (programmatic clients).
    if authorization.startswith("Bearer "):
        try:
            verify_api_key(api_key=api_key, authorization=authorization, auth_dev_mode=False)
            return
        except HTTPException:
            pass  # fall through to cookie check

    # Try cookie next (browser clients).
    cookie_token = request.cookies.get("dashboard_session", "")
    if cookie_token:
        # Reuse the API key as the JWT secret. Single-tenant, single-secret design.
        if verify_dashboard_jwt(cookie_token, secret=api_key) is not None:
            return

    raise HTTPException(
        status_code=401,
        detail="Authentication required: provide Bearer <api_key> or log in via /v2/auth/dashboard/login",
    )


require_dashboard_auth = Depends(_dashboard_auth_dependency)
