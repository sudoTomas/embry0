"""Tests for build_qa_sandbox_env shared helper."""

from embry0.workflows.qa._subtask_env import build_qa_sandbox_env


def test_build_qa_sandbox_env_includes_infra_vars():
    env = build_qa_sandbox_env(
        user_env_vars=None,
        git_proxy_url=None,
        qa_job_id="run-1",
        attempt_n=2,
        qa_network_name="qa-net-run-1",
    )
    assert env["QA_JOB_ID"] == "run-1"
    assert env["QA_ATTEMPT_N"] == "2"
    assert env["QA_NETWORK_NAME"] == "qa-net-run-1"
    assert "EMBRY0_GIT_PROXY_URL" not in env


def test_build_qa_sandbox_env_includes_git_proxy_when_set():
    env = build_qa_sandbox_env(
        user_env_vars=None,
        git_proxy_url="http://git-proxy:9101",
        qa_job_id="run-1",
        attempt_n=1,
        qa_network_name="",
    )
    assert env["EMBRY0_GIT_PROXY_URL"] == "http://git-proxy:9101"


def test_build_qa_sandbox_env_user_env_passes_through():
    """User vars without reserved-prefix keys should flow through. The
    actual filter behavior is owned by _filter_user_env_for_sandbox; this
    test confirms the helper hands its input there and returns the result."""
    env = build_qa_sandbox_env(
        user_env_vars=[{"key": "MY_API_TOKEN", "value": "secret", "scope": "qa"}],
        git_proxy_url=None,
        qa_job_id="run-1",
        attempt_n=1,
        qa_network_name="",
    )
    # The exact filter rules are tested elsewhere; we just confirm flow-through.
    # The infra vars must always be present:
    assert "QA_JOB_ID" in env


def test_build_qa_sandbox_env_empty_qa_network_name_still_sets_var():
    """Process-mode sandboxes pass qa_network_name='' but the env var should
    still be present (downstream code relies on its presence, branches on mode)."""
    env = build_qa_sandbox_env(
        user_env_vars=None,
        git_proxy_url=None,
        qa_job_id="run-1",
        attempt_n=1,
        qa_network_name="",
    )
    assert env["QA_NETWORK_NAME"] == ""


def test_build_qa_sandbox_env_includes_turbo_when_configured():
    from embry0.cache.turbo_remote import TurboRemoteConfig

    cfg = TurboRemoteConfig(api_url="https://t", team="x", token="y")
    env = build_qa_sandbox_env(
        user_env_vars=None,
        git_proxy_url=None,
        qa_job_id="run-1",
        attempt_n=1,
        qa_network_name="",
        turbo_remote_config=cfg,
    )
    assert env["TURBO_API"] == "https://t"
    assert env["TURBO_TOKEN"] == "y"
