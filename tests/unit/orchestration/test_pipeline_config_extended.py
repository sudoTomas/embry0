"""PipelineConfig and JobCreateRequest extended with new dimensions."""

from athanor.api.schemas import JobCreateRequest
from athanor.orchestration.state import PipelineConfig


def test_pipeline_config_accepts_execution_modes() -> None:
    cfg: PipelineConfig = {
        "sandbox_profile": "default",
        "max_feedback_loops": 2,
        "reviewer_enabled": True,
        "validator_modes": [],
        "agent_models": {},
        "agent_tools": {},
        "agent_skills": {},
        "budget_usd": 5.0,
        "execution_modes": {"developer": "sdk"},
        "auth_modes": {"developer": "api_key"},
        "system_prompts": {},
        "mcp_servers": {},
    }
    assert cfg["execution_modes"]["developer"] == "sdk"


def test_job_create_request_has_mode_overrides() -> None:
    req = JobCreateRequest(
        repo="example/repo",
        task="fix bug",
        execution_mode_override="sdk",
        auth_mode_override="api_key",
    )
    assert req.execution_mode_override == "sdk"
    assert req.auth_mode_override == "api_key"


def test_job_create_request_defaults_overrides_to_none() -> None:
    req = JobCreateRequest(repo="example/repo", task="fix bug")
    assert req.execution_mode_override is None
    assert req.auth_mode_override is None
