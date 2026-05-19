"""Test that IssueExecutor dispatches pipeline='qa' to QAWorkflow."""

from unittest.mock import MagicMock


def _build_executor() -> object:
    """Construct a bare IssueExecutor without exercising __init__'s wiring.

    ``_select_workflow`` only reads the pipeline argument, so attributes can
    be left unset.
    """
    from athanor.services.issue_executor import IssueExecutor

    return IssueExecutor.__new__(IssueExecutor)


def test_select_workflow_qa_returns_qa_workflow() -> None:
    """pipeline='qa' returns a QAWorkflow instance."""
    from athanor.workflows.qa.graph import QAWorkflow

    executor = _build_executor()
    wf = executor._select_workflow("qa")
    assert isinstance(wf, QAWorkflow)


def test_select_workflow_default_returns_issue_to_pr() -> None:
    """pipeline='issue-to-pr' returns IssueToprWorkflow."""
    from athanor.workflows.issue_to_pr.graph import IssueToprWorkflow

    executor = _build_executor()
    wf = executor._select_workflow("issue-to-pr")
    assert isinstance(wf, IssueToprWorkflow)


def test_select_workflow_unknown_falls_back_to_issue_to_pr() -> None:
    """Unknown pipeline names fall back to the legacy default rather than raising.

    Production callers can pass arbitrary template ids via pipeline_template;
    silently routing them to the default workflow preserves prior behaviour.
    """
    from athanor.workflows.issue_to_pr.graph import IssueToprWorkflow

    executor = _build_executor()
    wf = executor._select_workflow("some-future-pipeline")
    assert isinstance(wf, IssueToprWorkflow)


def test_build_graph_config_includes_qa_deps() -> None:
    """_build_graph_config surfaces QA deps under config['configurable']."""
    from athanor.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._config = None
    executor._sandbox = None
    executor._agent_runner = MagicMock(name="agent_runner")
    executor._proxy = MagicMock(name="proxy_manager")
    executor._issues = MagicMock(name="issues_repo")
    executor._inputs = MagicMock(name="inputs_repo")
    executor._db = None
    executor._repo_prefs = MagicMock(name="repo_prefs")
    executor._traces = MagicMock(name="traces_repo")
    executor._qa_minio = MagicMock(name="qa_minio")
    executor._qa_minio_sandbox = MagicMock(name="qa_minio_sandbox")
    executor._qa_token_registry = MagicMock(name="qa_token_registry")
    executor._profiles_repo = MagicMock(name="profiles_repo")
    executor._agent_sessions_repo = MagicMock(name="agent_sessions_repo")
    executor._qa_app_results_repo = MagicMock(name="qa_app_results_repo")
    executor._qa_image_tags_repo = MagicMock(name="qa_image_tags_repo")
    executor._qa_volume_state_repo = MagicMock(name="qa_volume_state_repo")
    executor._qa_shared_volume_manager = MagicMock(name="qa_shared_volume_manager")
    # Phase 5C: in-process SSE event bus.
    executor._qa_event_bus = MagicMock(name="qa_event_bus")
    # Phase 5D: affected-set persistence repo.
    executor._qa_run_metadata_repo = MagicMock(name="qa_run_metadata_repo")
    # Phase 5G: dashboard-set workspace_provider overrides repo.
    executor._qa_workspace_provider_overrides_repo = MagicMock(name="qa_workspace_provider_overrides_repo")
    executor._github_token = "test-github-token"

    cfg = executor._build_graph_config("job-123")
    configurable = cfg["configurable"]
    assert configurable["qa_minio"] is executor._qa_minio
    assert configurable["qa_minio_sandbox"] is executor._qa_minio_sandbox
    assert configurable["qa_token_registry"] is executor._qa_token_registry
    assert configurable["profiles_repo"] is executor._profiles_repo
    assert configurable["agent_sessions_repo"] is executor._agent_sessions_repo
    assert configurable["qa_app_results_repo"] is executor._qa_app_results_repo
    assert configurable["qa_image_tags_repo"] is executor._qa_image_tags_repo
    assert configurable["qa_volume_state_repo"] is executor._qa_volume_state_repo
    assert configurable["qa_shared_volume_manager"] is executor._qa_shared_volume_manager
    assert configurable["qa_event_bus"] is executor._qa_event_bus
    assert configurable["qa_run_metadata_repo"] is executor._qa_run_metadata_repo
    assert configurable["qa_workspace_provider_overrides_repo"] is executor._qa_workspace_provider_overrides_repo
    assert configurable["github_token"] == "test-github-token"
