"""Phase-A3: _build_graph_config must include 5 new keys so the QA
workflow's nodes can read them from config['configurable']."""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_executor():
    from embry0.services.issue_executor import IssueExecutor

    return IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=MagicMock(),
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="postgresql://test/test",
        qa_app_results_repo=MagicMock(name="qa_app_results_repo"),
        qa_image_tags_repo=MagicMock(name="qa_image_tags_repo"),
        qa_volume_state_repo=MagicMock(name="qa_volume_state_repo"),
        qa_shared_volume_manager=MagicMock(name="qa_shared_volume_manager"),
        github_token="test-github-token",
    )


def test_configurable_contains_qa_app_results_repo():
    executor = _make_executor()
    cfg = executor._build_graph_config(job_id="job-1")
    assert cfg["configurable"]["qa_app_results_repo"] is executor._qa_app_results_repo


def test_configurable_contains_qa_image_tags_repo():
    executor = _make_executor()
    cfg = executor._build_graph_config(job_id="job-1")
    assert cfg["configurable"]["qa_image_tags_repo"] is executor._qa_image_tags_repo


def test_configurable_contains_qa_volume_state_repo():
    executor = _make_executor()
    cfg = executor._build_graph_config(job_id="job-1")
    assert cfg["configurable"]["qa_volume_state_repo"] is executor._qa_volume_state_repo


def test_configurable_contains_qa_shared_volume_manager():
    executor = _make_executor()
    cfg = executor._build_graph_config(job_id="job-1")
    assert cfg["configurable"]["qa_shared_volume_manager"] is executor._qa_shared_volume_manager


def test_configurable_contains_github_token():
    executor = _make_executor()
    cfg = executor._build_graph_config(job_id="job-1")
    assert cfg["configurable"]["github_token"] == "test-github-token"


def test_configurable_preserves_existing_keys():
    """Smoke check that A3 didn't accidentally remove any of the existing
    keys established by Phases 1-4."""
    executor = _make_executor()
    cfg = executor._build_graph_config(job_id="job-1")
    keys = cfg["configurable"]
    for required in (
        "thread_id",
        "job_id",
        "agent_runner",
        "sandbox_manager",
        "proxy_manager",
        "docker",
        "issues_repo",
        "inputs_repo",
        "db",
        "credentials",
        "qa_minio",
        "qa_minio_sandbox",
        "qa_token_registry",
        "profiles_repo",
        "agent_sessions_repo",
    ):
        assert required in keys, f"missing required key: {required}"
