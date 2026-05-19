"""Phase-A integration: booted-app exposes QA pipeline wiring end-to-end.

Drives the same code path that runs in production:
  create_app() -> _init_app_state(...) -> app.state.issue_executor ->
  executor._build_graph_config(job_id) -> config["configurable"]

Verifies that all 5 Phase-A keys land in the LangGraph configurable
dict that the QA workflow nodes read from. Catches regressions like
'someone added qa_foo_repo to app.state but forgot to pass it to
IssueExecutor.__init__ AND to _build_graph_config'."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def test_full_qa_wiring_round_trip():
    from athanor.api.app import _init_app_state, create_app

    app = create_app()
    db = MagicMock()
    asyncio.run(
        _init_app_state(
            app=app,
            db=db,
            database_url="postgresql://test/test",
            github_token="test-token-32-characters-or-longer-x",
        )
    )

    executor = app.state.issue_executor
    cfg = executor._build_graph_config(job_id="round-trip-job")

    configurable = cfg["configurable"]

    # Identity check: the same object that lives on app.state must be
    # the same one in the configurable dict. Reference equality
    # (`is`) — not just truthy.
    assert configurable["qa_app_results_repo"] is app.state.qa_app_results_repo
    assert configurable["qa_image_tags_repo"] is app.state.qa_image_tags_repo
    assert configurable["qa_volume_state_repo"] is app.state.qa_volume_state_repo

    # Note: qa_shared_volume_manager is set to None in _init_app_state and
    # only filled in by the lifespan AFTER app.state.docker is initialized.
    # In this test we drive _init_app_state directly without the rest of
    # the lifespan, so we can only assert that the executor and app.state
    # are CONSISTENT — both either None, or both pointing at the same
    # instance once a real lifespan runs.
    assert configurable["qa_shared_volume_manager"] is app.state.qa_shared_volume_manager

    # github_token is a string, not an object on app.state — assert by value.
    assert configurable["github_token"] == "test-token-32-characters-or-longer-x"


def test_full_qa_wiring_round_trip_no_github_token():
    """When _init_app_state is called without a github_token (auth_dev_mode
    or unconfigured prod), configurable['github_token'] is None — and
    qa_report_node short-circuits gracefully (existing behavior)."""
    from athanor.api.app import _init_app_state, create_app

    app = create_app()
    db = MagicMock()
    asyncio.run(
        _init_app_state(
            app=app,
            db=db,
            database_url="postgresql://test/test",
            github_token=None,
        )
    )
    executor = app.state.issue_executor
    cfg = executor._build_graph_config(job_id="no-token-job")
    assert cfg["configurable"]["github_token"] is None
