"""qa_report_node — orchestrator-side aggregation report.

Runs after qa_orchestrator_node has aggregated per-app results. Writes the
sticky comment with the aggregate verdict and per-app breakdown
(idempotent, by COMMENT_MARKER) on the forge init_orchestrator_node resolved
(`state['qa']['forge']`): a GitHub issue comment or a GitLab issue note. A
job with no issue number (a standalone QA job) has nothing to comment on
and skips.

The sticky comment is the only QA reporting surface. This node used to also
write an aggregate `embry0/qa` GitHub Check Run, but GitHub's Checks API
accepts writes only from a GitHub App installation token — a personal access
token or OAuth credential is rejected with 403 on every call, for every repo.
embry0 authenticates with PATs by design, so that write could never succeed
and was removed rather than retried. The comment already carries the same
verdict, per-app status, durations and failure detail.

Persistence to qa_app_results was done by qa_orchestrator_node already.
This node is purely the forge-facing reporting surface.

Configurable deps:
  - github_token (str): orchestrator-side default token, fed in via
    config['configurable']. Resolved per repo through `forge_token` (the
    credential registry when `credential_resolver` is in configurable, else
    the GITHUB_TOKEN__<OWNER> env override) before use; falls back to this
    default when no repo-specific token is found. GitHub only.
  - embry0_config, credential_resolver: build the GitLab client
    (`make_gitlab_client`) for a GitLab forge.
  - repos_repo: resolves the forge when state predates `qa.forge`.

State reads:
  - state['qa']['outcome']         (dict from OrchestratorOutcome)
  - state['qa']['per_app_results'] (list of SubTaskResult.to_dict())
  - state['qa']['apps_to_qa']      (list[str])
  - state['qa']['validation_warnings']
  - state['qa']['forge']           ("github" | "gitlab"; resolved if absent)
  - state['repo']                  ('owner/repo')
  - state['forge_issue_number']    (GitHub number / GitLab iid; None skips the write)
  - state['issue_number']          (fallback when forge_issue_number is absent:
                                    states written before it existed)
  - state.get('attempt_number', 1) (used as run_number in the comment)

Failures here log warnings but DO NOT fail the run — final_status was set by
the orchestrator already, and a missing forge-side surface is not a QA verdict.

Security boundary: the `github_token` is orchestrator-side only, resolved
per repo (see `forge_token`), and MUST NOT be propagated into
sub-task config['configurable']. Sub-tasks read their own credentials via
the per-sandbox proxy + sandbox_token. Reviewers of future cache-layer work
in Phase 2: do not forward github_token through to sub-task subgraph
invocations.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from embry0.execution import events
from embry0.execution.github_client import GitHubClient
from embry0.notifications.qa_comment import COMMENT_MARKER, render_qa_comment, upsert_sticky_comment
from embry0.services.forge_resolver import resolve_forge
from embry0.workflows.qa.subtask_result_schema import (
    CacheHits,
    SubTaskResult,
    SubTaskStatus,
)

logger = structlog.get_logger(__name__)


def _hydrate_results(per_app_dicts: list[dict[str, Any]]) -> list[SubTaskResult]:
    """Reverse of SubTaskResult.to_dict() — reconstruct dataclasses for the
    rendering + check-writer layers."""
    out: list[SubTaskResult] = []
    for d in per_app_dicts:
        ch = d.get("cache_hits") or {}
        out.append(
            SubTaskResult(
                app_name=d["app_name"],
                status=SubTaskStatus(d["status"]),
                duration_ms=int(d.get("duration_ms", 0)),
                cache_hits=CacheHits(
                    prebaked_image=bool(ch.get("prebaked_image", False)),
                    shared_volume=bool(ch.get("shared_volume", False)),
                    turbo_remote_hits=list(ch.get("turbo_remote_hits") or []),
                    turbo_remote_misses=list(ch.get("turbo_remote_misses") or []),
                ),
                failure_summary=d.get("failure_summary"),
                raw_result=d.get("raw_result", {}) or {},
                boot_phase=d.get("boot_phase"),
            )
        )
    return out


async def qa_report_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Write the sticky QA comment on the job's forge.

    Best-effort: failures are logged and the run continues. final_status is
    already set by qa_orchestrator_node; this node is reporting only.
    Brackets itself with ``node_started``/``node_completed`` on the job
    stream (spec 3.7).
    """
    try:
        writer = get_stream_writer()
    except RuntimeError:  # outside a runnable context (unit tests)
        writer = lambda _event: None  # noqa: E731
    writer(events.node_started(node="qa_report"))
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}

    qa_state = state.get("qa") or {}
    outcome = qa_state.get("outcome") or {}
    per_app = qa_state.get("per_app_results") or []
    apps_to_qa = qa_state.get("apps_to_qa") or []
    validation_warnings = qa_state.get("validation_warnings") or []

    repo = state.get("repo")
    # forge_issue_number carries a GitLab iid too; issue_number is GitHub-only
    # and is read only for states written before the forge-neutral key.
    number = state["forge_issue_number"] if "forge_issue_number" in state else state.get("issue_number")
    run_number = int(state.get("attempt_number") or 1)
    overall_status = outcome.get("overall_status") or "infra_error"
    failure_summary = outcome.get("failure_summary")

    if number is None or not repo:
        # A standalone QA job has no issue or pull request to comment on;
        # the dashboard and the job row carry its verdict.
        logger.info("qa_report_skipped_no_change_request", repo=repo)
        writer(events.node_completed(node="qa_report"))
        return {}

    resolver = configurable.get("credential_resolver")
    forge = qa_state.get("forge")
    if not forge:
        try:
            forge = await resolve_forge(configurable.get("repos_repo"), repo, job_id=str(state.get("job_id") or ""))
        except Exception:
            logger.warning("qa_report_forge_lookup_failed", repo=repo, exc_info=True)
            forge = "github"

    body = render_qa_comment(
        run_number=run_number,
        overall_status=overall_status,
        apps_to_qa=apps_to_qa,
        results=_hydrate_results(per_app),
        failure_summary=failure_summary,
        validation_warnings=validation_warnings,
    )

    if forge == "gitlab":
        from embry0.execution.gitlab_client import make_gitlab_client

        try:
            client = make_gitlab_client(configurable.get("embry0_config"), credential_resolver=resolver)
            await client.upsert_sticky_issue_note(repo, int(number), body, marker=COMMENT_MARKER)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qa_comment_upsert_failed", forge="gitlab", repo=repo, issue_number=number, error=str(exc))
        writer(events.node_completed(node="qa_report"))
        return {}

    default_token = configurable.get("github_token")
    from embry0.services.credentials.resolver import forge_token

    try:
        github_token = await forge_token(resolver, repo, "github", default_token or "") or default_token
    except Exception:
        # E.g. a pinned credential that is unavailable: reporting never fails the run.
        logger.warning("qa_report_token_lookup_failed", repo=repo, exc_info=True)
        github_token = ""
    if not github_token:
        logger.warning("qa_report_skipped_no_forge_token", forge="github", repo=repo)
        writer(events.node_completed(node="qa_report"))
        return {}

    try:
        # The client carries the resolver, so the token sent is the one
        # resolved above — not a second, env-only lookup inside the client.
        await upsert_sticky_comment(
            github_token=github_token,
            repo=repo,
            issue_number=number,
            body=body,
            client=GitHubClient(default_token=default_token or "", credential_resolver=resolver),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("qa_comment_upsert_failed", forge="github", repo=repo, issue_number=number, error=str(exc))

    writer(events.node_completed(node="qa_report"))
    return {}
