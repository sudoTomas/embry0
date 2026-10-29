from collections.abc import Callable

import pytest
from pydantic import ValidationError

from embry0.api.schemas import (
    AgentCreateRequest,
    BudgetConfigResponse,
    ContextType,
    JobContext,
    JobCreateRequest,
    JobResponse,
    QAPresignBatchRequest,
    SandboxProfileRequest,
)
from embry0.api.schemas.credentials import CredentialCreate
from embry0.api.v1.bootstrap import BootstrapRequest


def test_job_create_request_valid():
    req = JobCreateRequest(repo="owner/repo", task="Fix the bug")
    assert req.repo == "owner/repo"
    assert req.task == "Fix the bug"


def test_job_create_request_validates_repo_format():
    with pytest.raises(ValidationError):
        JobCreateRequest(repo="invalid", task="Fix the bug")


def test_job_create_request_rejects_empty_task():
    with pytest.raises(ValidationError):
        JobCreateRequest(repo="owner/repo", task="")


def test_job_create_request_budget_limit():
    with pytest.raises(ValidationError):
        JobCreateRequest(repo="owner/repo", task="Fix", max_budget_usd=2000.0)


def test_job_response_serialization():
    resp = JobResponse(
        job_id="job-abc123",
        status="pending",
        repo="owner/repo",
        task="Fix bug",
        total_cost_usd=0.0,
    )
    data = resp.model_dump()
    assert data["job_id"] == "job-abc123"
    assert data["status"] == "pending"


def test_job_response_exposes_issue_id_and_current_stage():
    """Console board fields: issue_id (operator-job signal) and current_stage
    (stage badge from the poll alone) round-trip through the response model."""
    resp = JobResponse(
        job_id="job-abc123",
        status="running",
        repo="owner/repo",
        task="Fix bug",
        issue_id="iss-42",
        current_stage="developer_complete",
    )
    data = resp.model_dump()
    assert data["issue_id"] == "iss-42"
    assert data["current_stage"] == "developer_complete"


def test_job_response_tolerates_legacy_rows_without_stage_or_issue():
    """Legacy rows carry NULL issue_id/current_stage (or predate the columns
    entirely) — the model must default both to None."""
    resp = JobResponse(job_id="job-old", status="completed", repo="owner/repo", task="Old job")
    assert resp.issue_id is None
    assert resp.current_stage is None


def test_sandbox_profile_request():
    req = SandboxProfileRequest(name="python-3.12", base_image="embry0-sandbox-python:3.12")
    assert req.name == "python-3.12"


def test_sandbox_profile_request_accepts_valid_extra_hosts():
    req = SandboxProfileRequest(
        name="qa-external-acme",
        extra_hosts={"app.example.com": "192.168.1.10", "ipv6.local": "::1"},
    )
    assert req.extra_hosts["app.example.com"] == "192.168.1.10"


def test_sandbox_profile_request_rejects_non_ip_extra_host_value():
    with pytest.raises(ValidationError):
        SandboxProfileRequest(name="p", extra_hosts={"app.local": "not-an-ip"})


def test_sandbox_profile_request_rejects_invalid_extra_host_name():
    with pytest.raises(ValidationError):
        SandboxProfileRequest(name="p", extra_hosts={"bad host!": "10.0.0.1"})


def test_sandbox_profile_request_rejects_reserved_dind_alias():
    with pytest.raises(ValidationError):
        SandboxProfileRequest(name="p", extra_hosts={"dind": "10.0.0.1"})


def test_budget_config_response():
    resp = BudgetConfigResponse(
        max_budget_per_job_usd=10.0,
        daily_cap_usd=100.0,
        monthly_cap_usd=500.0,
        rate_limit_per_author_per_hour=5,
        overrun_mode="soft",
    )
    assert resp.overrun_mode == "soft"


def test_legacy_repo_task_coerces_to_git_context():
    req = JobCreateRequest(repo="owner/repo", task="Fix the bug")
    assert req.repo == "owner/repo"
    assert req.context is not None
    assert req.context.type == ContextType.git
    assert req.context.repo == "owner/repo"


def test_bare_task_defaults_to_none_context():
    req = JobCreateRequest(task="Research the market")
    assert req.repo is None
    assert req.context.type == ContextType.none


def test_explicit_http_context_without_repo():
    req = JobCreateRequest(task="Summarize", context=JobContext(type=ContextType.http, url="https://x/y"))
    assert req.context.type == ContextType.http


def test_conflicting_repo_and_context_rejected():
    with pytest.raises(ValidationError):
        JobCreateRequest(
            repo="owner/repo",
            task="x",
            context=JobContext(type=ContextType.http, url="https://x/y"),
        )


def test_qa_pipeline_still_requires_repo():
    with pytest.raises(ValidationError):
        JobCreateRequest(pipeline="qa", branch="feat/x", task="qa")


def test_every_repo_schema_accepts_a_gitlab_subgroup_path():
    """Spec 3.5: one rule, four schemas. `POST /repos` always accepted nested
    paths; /jobs, /issues and /repos/bootstrap 422'd them until now."""
    from embry0.api.schemas import ContextType, JobContext, JobCreateRequest
    from embry0.api.schemas.issues import CreateIssueRequest, UpdateIssueRequest
    from embry0.api.v1.bootstrap import BootstrapRequest
    from embry0.api.v1.repos import RepoRegisterRequest

    nested = "acme/group/backend"
    assert JobCreateRequest(repo=nested, task="Fix").repo == nested
    assert JobContext(type=ContextType.git, repo=nested).repo == nested
    assert CreateIssueRequest(title="t", repo=nested).repo == nested
    assert UpdateIssueRequest(repo=nested).repo == nested
    assert BootstrapRequest(repo=nested).repo == nested
    assert RepoRegisterRequest(repo=nested).repo == nested


def test_every_repo_schema_still_rejects_a_single_segment():
    from embry0.api.schemas import JobCreateRequest
    from embry0.api.schemas.issues import CreateIssueRequest
    from embry0.api.v1.bootstrap import BootstrapRequest
    from embry0.api.v1.repos import RepoRegisterRequest

    for factory in (
        lambda: JobCreateRequest(repo="noslash", task="Fix"),
        lambda: CreateIssueRequest(title="t", repo="noslash"),
        lambda: BootstrapRequest(repo="noslash"),
        lambda: RepoRegisterRequest(repo="noslash"),
    ):
        with pytest.raises(ValidationError):
            factory()


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: AgentCreateRequest(type="developer\n", description="d", model="m"), id="agent type"),
        pytest.param(lambda: QAPresignBatchRequest(sandbox_token="t" * 32, paths=["result.json\n"]), id="qa path"),
        pytest.param(lambda: SandboxProfileRequest(name="p", extra_hosts={"app.example.com\n": "10.0.0.1"}), id="host"),
        pytest.param(
            lambda: CredentialCreate(
                kind="tailnet_api_key",
                provider="tailscale",
                label="tailnet",
                secret="key-000000000001",
                metadata={"tailnet": "example.com\n"},
            ),
            id="tailnet",
        ),
        pytest.param(lambda: BootstrapRequest(repo="acme/widgets", app="web\n"), id="bootstrap app"),
    ],
)
def test_a_request_value_ending_in_a_newline_is_refused(build: Callable[[], object]) -> None:
    """Python's ``$`` also matches just before a final newline, so ``match`` took ``name\\n`` for ``name``."""
    try:
        accepted = build()
    except ValidationError:
        return
    raise AssertionError(f"a value ending in a newline must be refused: {accepted!r}")
