import pytest
from pydantic import ValidationError

from athanor.api.schemas import (
    BudgetConfigResponse,
    JobCreateRequest,
    JobResponse,
    SandboxProfileRequest,
)


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


def test_sandbox_profile_request():
    req = SandboxProfileRequest(name="python-3.12", base_image="athanor-sandbox-python:3.12")
    assert req.name == "python-3.12"


def test_budget_config_response():
    resp = BudgetConfigResponse(
        max_budget_per_job_usd=10.0,
        daily_cap_usd=100.0,
        monthly_cap_usd=500.0,
        rate_limit_per_author_per_hour=5,
        overrun_mode="soft",
    )
    assert resp.overrun_mode == "soft"
