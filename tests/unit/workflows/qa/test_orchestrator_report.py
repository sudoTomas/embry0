"""Tests for qa_report_node — sticky QA PR comment writer."""

from __future__ import annotations

import os
from unittest.mock import ANY, AsyncMock

import pytest

from embry0.notifications.qa_comment import COMMENT_MARKER
from embry0.workflows.qa.orchestrator_report import qa_report_node


@pytest.fixture(autouse=True)
def _clean_owner_tokens(monkeypatch):
    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)


def _state(**qa_overrides):
    qa = {
        "outcome": {
            "overall_status": "passed",
            "apps_to_qa": ["hub"],
            "failure_summary": None,
            "validation_errors": [],
            "validation_warnings": [],
        },
        "per_app_results": [
            {
                "app_name": "hub",
                "status": "passed",
                "duration_ms": 1000,
                "cache_hits": {
                    "prebaked_image": False,
                    "shared_volume": False,
                    "turbo_remote_hits": [],
                    "turbo_remote_misses": [],
                },
                "failure_summary": None,
                "raw_result": {},
            }
        ],
        "apps_to_qa": ["hub"],
        "head_sha": "abc123",
        "validation_warnings": [],
    }
    qa.update(qa_overrides)
    return {
        "job_id": "run-1",
        "repo": "org/repo",
        "issue_number": 42,
        "attempt_number": 1,
        "qa": qa,
    }


@pytest.mark.asyncio
async def test_writes_comment_on_happy_path(monkeypatch):
    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    config = {"configurable": {"github_token": "tok"}}
    out = await qa_report_node(_state(), config)
    assert out == {}

    written_comment.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_write_when_no_github_token(monkeypatch):
    written_comment = AsyncMock()
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    config = {"configurable": {}}  # no github_token
    await qa_report_node(_state(), config)
    written_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_comment_still_written_when_no_head_sha(monkeypatch):
    """head_sha is irrelevant to reporting now that the check run is gone."""
    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    config = {"configurable": {"github_token": "tok"}}
    out = await qa_report_node(_state(head_sha=None), config)
    assert out == {}

    written_comment.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_comment_when_no_issue_number(monkeypatch):
    written_comment = AsyncMock()
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    state = _state()
    state["issue_number"] = None
    config = {"configurable": {"github_token": "tok"}}
    await qa_report_node(state, config)

    written_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_comment_failure_does_not_fail_the_node(monkeypatch):
    """A GitHub-side failure logs a warning; the node still returns cleanly."""
    written_comment = AsyncMock(side_effect=RuntimeError("github 503"))
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    config = {"configurable": {"github_token": "tok"}}
    out = await qa_report_node(_state(), config)

    assert out == {}
    written_comment.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_run_passes_failure_summary_through(monkeypatch):
    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )

    config = {"configurable": {"github_token": "tok"}}
    state = _state(
        outcome={
            "overall_status": "infra_error",
            "apps_to_qa": [],
            "failure_summary": "qa.yaml not loaded",
            "validation_errors": [],
            "validation_warnings": [],
        }
    )
    await qa_report_node(state, config)

    body = written_comment.await_args.kwargs["body"]
    assert "qa.yaml not loaded" in body


@pytest.mark.asyncio
async def test_qa_report_resolves_per_owner_token(monkeypatch):
    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr(
        "embry0.workflows.qa.orchestrator_report.upsert_sticky_comment",
        written_comment,
    )
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "tok-rc")

    state = _state()
    state["repo"] = "acme-corp/widgets"
    config = {"configurable": {"github_token": "tok-default"}}
    await qa_report_node(state, config)

    assert written_comment.await_args.kwargs["github_token"] == "tok-rc"


@pytest.mark.asyncio
async def test_sticky_comment_client_sends_the_resolved_token(monkeypatch):
    """The token that gates the write must be the one sent: the client handed
    to upsert_sticky_comment carries the resolver, not a bare env lookup."""
    from types import SimpleNamespace

    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", written_comment)
    resolver = AsyncMock()
    resolver.resolve_forge = AsyncMock(return_value=SimpleNamespace(secret="registry-tok"))

    await qa_report_node(_state(), {"configurable": {"github_token": "tok", "credential_resolver": resolver}})

    client = written_comment.await_args.kwargs.get("client")
    assert client is not None, "upsert_sticky_comment built its own env-resolving client"
    assert client._credential_resolver is resolver


@pytest.mark.asyncio
async def test_a_run_without_a_repo_writes_nothing_and_looks_up_no_token(monkeypatch):
    """A missing repo leaves nothing to comment on: the node skips before any
    token lookup."""
    from types import SimpleNamespace

    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", written_comment)
    resolver = AsyncMock()
    resolver.resolve_forge = AsyncMock(return_value=SimpleNamespace(secret="registry-tok"))
    state = _state()
    state["repo"] = None

    out = await qa_report_node(state, {"configurable": {"github_token": "tok", "credential_resolver": resolver}})

    assert out == {}
    written_comment.assert_not_awaited()
    resolver.resolve_forge.assert_not_awaited()


@pytest.mark.asyncio
async def test_standalone_job_without_change_request_skips_before_any_token_lookup(monkeypatch):
    import structlog.testing

    async def no_lookup(*a, **kw):
        raise AssertionError("token lookup must not happen without an issue number")

    monkeypatch.setattr("embry0.services.credentials.resolver.forge_token", no_lookup)
    written_comment = AsyncMock()
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", written_comment)
    state = _state(forge="gitlab")
    state["issue_number"] = None
    with structlog.testing.capture_logs() as logs:
        out = await qa_report_node(state, {"configurable": {"github_token": "tok"}})
    assert out == {}
    written_comment.assert_not_awaited()
    assert any(log["event"] == "qa_report_skipped_no_change_request" for log in logs), [log["event"] for log in logs]


@pytest.mark.asyncio
async def test_gitlab_forge_writes_a_sticky_issue_note(monkeypatch):
    fake_client = AsyncMock()
    fake_client.upsert_sticky_issue_note = AsyncMock(return_value=5)
    made: dict = {}

    def fake_make(config, credential_resolver=None, **kw):
        made["config"] = config
        made["resolver"] = credential_resolver
        return fake_client

    monkeypatch.setattr("embry0.execution.gitlab_client.make_gitlab_client", fake_make)
    github_comment = AsyncMock()
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", github_comment)

    sentinel_config, sentinel_resolver = object(), object()
    out = await qa_report_node(
        _state(forge="gitlab"),
        {"configurable": {"embry0_config": sentinel_config, "credential_resolver": sentinel_resolver}},
    )
    assert out == {}
    github_comment.assert_not_awaited()
    fake_client.upsert_sticky_issue_note.assert_awaited_once_with("org/repo", 42, ANY, marker=COMMENT_MARKER)
    assert "hub" in fake_client.upsert_sticky_issue_note.await_args.args[2]
    assert made["config"] is sentinel_config and made["resolver"] is sentinel_resolver


@pytest.mark.asyncio
async def test_gitlab_note_targets_the_forge_issue_number(monkeypatch):
    # A GitLab-tracked issue has no GitHub number: the executor seeds
    # issue_number=None and the iid under forge_issue_number.
    fake_client = AsyncMock()
    fake_client.upsert_sticky_issue_note = AsyncMock(return_value=5)
    monkeypatch.setattr("embry0.execution.gitlab_client.make_gitlab_client", lambda *a, **kw: fake_client)
    state = _state(forge="gitlab")
    state["issue_number"] = None
    state["forge_issue_number"] = 7

    await qa_report_node(state, {"configurable": {}})

    assert fake_client.upsert_sticky_issue_note.await_count == 1, (
        "a GitLab issue (issue_number None, forge_issue_number 7) got no note"
    )
    assert fake_client.upsert_sticky_issue_note.await_args.args[1] == 7


@pytest.mark.asyncio
async def test_legacy_state_without_forge_resolves_it_from_the_registry(monkeypatch):
    fake_client = AsyncMock()
    fake_client.upsert_sticky_issue_note = AsyncMock(return_value=5)
    monkeypatch.setattr("embry0.execution.gitlab_client.make_gitlab_client", lambda *a, **kw: fake_client)
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", AsyncMock())

    async def resolve(repos_repo, repo, *, job_id=""):
        assert repos_repo == "the-registry"
        return "gitlab"

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.resolve_forge", resolve)
    state = _state()
    assert "forge" not in state["qa"]
    await qa_report_node(state, {"configurable": {"repos_repo": "the-registry"}})
    fake_client.upsert_sticky_issue_note.assert_awaited_once()


@pytest.mark.asyncio
async def test_gitlab_note_failure_is_logged_not_raised(monkeypatch):
    import structlog.testing

    fake_client = AsyncMock()
    fake_client.upsert_sticky_issue_note = AsyncMock(side_effect=RuntimeError("gitlab 502"))
    monkeypatch.setattr("embry0.execution.gitlab_client.make_gitlab_client", lambda *a, **kw: fake_client)
    with structlog.testing.capture_logs() as logs:
        out = await qa_report_node(_state(forge="gitlab"), {"configurable": {}})
    assert out == {}
    assert any(log["event"] == "qa_comment_upsert_failed" and log.get("forge") == "gitlab" for log in logs)


@pytest.mark.asyncio
async def test_github_without_token_logs_a_forge_neutral_skip(monkeypatch):
    import structlog.testing

    written_comment = AsyncMock()
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", written_comment)
    with structlog.testing.capture_logs() as logs:
        await qa_report_node(_state(forge="github"), {"configurable": {}})
    written_comment.assert_not_awaited()
    skip = [log for log in logs if log["event"] == "qa_report_skipped_no_forge_token"]
    assert skip and skip[0]["forge"] == "github", [log["event"] for log in logs]


@pytest.mark.asyncio
async def test_forge_lookup_failure_falls_back_to_github(monkeypatch):
    import structlog.testing

    written_comment = AsyncMock(return_value=99)
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.upsert_sticky_comment", written_comment)

    async def broken_resolve(*a, **kw):
        raise RuntimeError("registry down")

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_report.resolve_forge", broken_resolve)
    state = _state()
    assert "forge" not in state["qa"]
    with structlog.testing.capture_logs() as logs:
        out = await qa_report_node(state, {"configurable": {"github_token": "tok"}})
    assert out == {}
    written_comment.assert_awaited_once()
    assert written_comment.await_args.kwargs["github_token"] == "tok"
    assert any(log["event"] == "qa_report_forge_lookup_failed" for log in logs), [log["event"] for log in logs]
