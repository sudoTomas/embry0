"""Production wires the pull request reviewer (#30).

Read from the AST of app.py, as test_merge_approvals_wiring.py does: the
lifespan needs Docker and a database. Every collaborator below is optional
at construction, so a missing one would fail silently at the first review.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import embry0.api.app as app_module

REQUIRED = ("config", "repos_repo", "prefs_repo", "jobs_repo", "reviews_repo", "executor", "github", "gitlab")


def _tree() -> ast.Module:
    return ast.parse(pathlib.Path(app_module.__file__).read_text())


def _call(name: str) -> ast.Call:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name:
            return node
    pytest.fail(f"embry0/api/app.py never calls {name}")


def test_the_dispatcher_gets_every_collaborator() -> None:
    passed = {kw.arg: kw.value for kw in _call("PrReviewDispatcher").keywords if kw.arg}
    missing = [name for name in REQUIRED if name not in passed]
    assert not missing, f"PrReviewDispatcher is built without {missing}"
    assert ast.unparse(passed["reviews_repo"]).startswith("PrReviewsRepository(")
    assert ast.unparse(passed["executor"]) == "app.state.issue_executor"


def test_the_dispatcher_is_where_the_webhooks_look_for_it() -> None:
    targets = {ast.unparse(t) for node in ast.walk(_tree()) if isinstance(node, ast.Assign) for t in node.targets}
    assert "app.state.pr_review_dispatcher" in targets


def test_the_reviewer_agent_is_seeded_at_startup() -> None:
    _call("seed_pr_reviewer_agent")
