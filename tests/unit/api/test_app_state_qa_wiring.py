"""Phase-A wiring: confirm create_app() exposes QA-pipeline state attrs.

Pure-Python; no DB / Docker / MinIO needed because we drive create_app()
without entering the lifespan (which would attempt those connections).
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_state_with_db():
    """Run the body of _init_app_state with a stubbed DB so we can inspect
    app.state attrs without a real Postgres or lifespan."""
    from athanor.api.app import _init_app_state, create_app

    app = create_app()
    db = MagicMock()  # DatabasePool stand-in; the repo constructors only
                      # store the reference and don't call anything on it
                      # at __init__ time.

    import asyncio
    asyncio.run(_init_app_state(
        app=app,
        db=db,
        database_url="postgresql://test/test",
        github_token="test-token-32-characters-or-longer-x",
    ))

    # Simulate the lifespan's docker initialization and manager construction
    # (the real lifespan does this at line ~481 and ~645, but the test
    # short-circuits by calling _init_app_state directly).
    app.state.docker = MagicMock()
    from athanor.cache.volume_manager import SharedVolumeManager
    real_manager = SharedVolumeManager(
        docker=app.state.docker,
        state_repo=app.state.qa_volume_state_repo,
    )
    app.state.qa_shared_volume_manager = real_manager
    # Also update the executor's reference, matching the lifespan behavior
    # where the manager is set after the executor is constructed with None.
    app.state.issue_executor._qa_shared_volume_manager = real_manager

    return app


def test_qa_app_results_repo_on_state():
    app = _make_state_with_db()
    assert getattr(app.state, "qa_app_results_repo", None) is not None


def test_qa_image_tags_repo_on_state():
    app = _make_state_with_db()
    assert getattr(app.state, "qa_image_tags_repo", None) is not None


def test_qa_volume_state_repo_on_state():
    app = _make_state_with_db()
    assert getattr(app.state, "qa_volume_state_repo", None) is not None


def test_qa_shared_volume_manager_on_state():
    app = _make_state_with_db()
    assert getattr(app.state, "qa_shared_volume_manager", None) is not None


def test_github_token_threaded_to_issue_executor():
    """Phase-A2 + A3 also touch this — but A1 is the foundation; assert
    that the executor was given the github_token kwarg."""
    app = _make_state_with_db()
    executor = app.state.issue_executor
    assert getattr(executor, "_github_token", None) == "test-token-32-characters-or-longer-x"


def test_executor_carries_qa_image_tags_repo():
    app = _make_state_with_db()
    assert app.state.issue_executor._qa_image_tags_repo is not None


def test_executor_carries_qa_volume_state_repo():
    app = _make_state_with_db()
    assert app.state.issue_executor._qa_volume_state_repo is not None


def test_executor_carries_qa_shared_volume_manager():
    app = _make_state_with_db()
    assert app.state.issue_executor._qa_shared_volume_manager is not None


def test_executor_carries_qa_app_results_repo():
    """Phase-4 B2 instantiated this on app.state but did NOT pass it into
    the executor. Fix here so _build_graph_config can include it."""
    app = _make_state_with_db()
    assert app.state.issue_executor._qa_app_results_repo is not None
