"""Production hands the settings store to the executor, so a coding job reads its settings (#182).

``IssueExecutor`` takes the store as an optional argument and constructs
happily without it: every job then runs on the sections' defaults, which
looks exactly like an operator who changed nothing. So this builds the app
state the way production does and checks the executor got the very store
the settings API writes to.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def test_the_executor_reads_the_store_the_settings_api_writes() -> None:
    from embry0.api.app import _init_app_state, create_app

    app = create_app()
    asyncio.run(
        _init_app_state(
            app=app,
            db=MagicMock(),  # the repositories only keep the pool at construction
            database_url="postgresql://test/test",
            github_token="test-token-32-characters-or-longer-x",
        )
    )
    executor = app.state.issue_executor
    assert app.state.settings is not None
    assert getattr(executor, "_settings", None) is app.state.settings, (
        "the issue executor must be built with app.state.settings: without it every job runs on the "
        "done_when and salvage defaults, whatever the settings document says"
    )
