"""qa_report_node — orchestrator-side aggregation report.

Runs after qa_orchestrator_node has aggregated per-app results. Writes:
  1. The aggregate `athanor/qa` GitHub Check Run (idempotent, by head_sha).
  2. The sticky PR comment with per-app breakdown (idempotent, by COMMENT_MARKER).

Persistence to qa_app_results was done by qa_orchestrator_node already.
This node is purely the GitHub-facing reporting surface.

Configurable deps:
  - github_token (str): orchestrator-side token. Fed in via config['configurable'].

State reads:
  - state['qa']['outcome']         (dict from OrchestratorOutcome)
  - state['qa']['per_app_results'] (list of SubTaskResult.to_dict())
  - state['qa']['apps_to_qa']      (list[str])
  - state['qa']['head_sha']        (set by init_orchestrator_node)
  - state['qa']['validation_warnings']
  - state['repo']                  ('owner/repo')
  - state['issue_number']          (the PR number for issue→PR jobs)
  - state.get('attempt_number', 1) (used as run_number in the comment)

Failures here log warnings but DO NOT fail the run — final_status was set by
the orchestrator already, and a missing GitHub-side surface is not a QA verdict.

Security boundary: the `github_token` is orchestrator-side only and MUST
NOT be propagated into sub-task config['configurable']. Sub-tasks read
their own credentials via the per-sandbox proxy + sandbox_token. Reviewers
of future cache-layer work in Phase 2: do not forward github_token through
to sub-task subgraph invocations.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig

from athanor.notifications.qa_check import write_aggregate_check
from athanor.notifications.qa_comment import render_qa_comment, upsert_sticky_comment
from athanor.workflows.qa.subtask_result_schema import (
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
                    turbo_remote=bool(ch.get("turbo_remote", False)),
                ),
                trace_url=d.get("trace_url"),
                failure_summary=d.get("failure_summary"),
                raw_result=d.get("raw_result", {}) or {},
            )
        )
    return out


async def qa_report_node(
    state: dict[str, Any],
    config: RunnableConfig,
) -> dict[str, Any]:
    """Write the athanor/qa check + sticky PR comment.

    Best-effort: failures are logged and the run continues. final_status is
    already set by qa_orchestrator_node; this node is reporting only.
    """
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    github_token = configurable.get("github_token")

    qa_state = state.get("qa") or {}
    outcome = qa_state.get("outcome") or {}
    per_app = qa_state.get("per_app_results") or []
    apps_to_qa = qa_state.get("apps_to_qa") or []
    head_sha = qa_state.get("head_sha")
    validation_warnings = qa_state.get("validation_warnings") or []

    repo = state.get("repo")
    issue_number = state.get("issue_number")
    run_number = int(state.get("attempt_number") or 1)
    overall_status = outcome.get("overall_status") or "infra_error"
    failure_summary = outcome.get("failure_summary")

    # If we have no GitHub token or no repo, skip both writes — log a warning.
    if github_token is None or not repo:
        logger.warning(
            "qa_report_skipped_no_github_token_or_repo",
            has_token=github_token is not None,
            repo=repo,
        )
        return {}

    results = _hydrate_results(per_app)

    # 1. Aggregate check
    if head_sha:
        try:
            await write_aggregate_check(
                github_token=github_token,
                repo=repo,
                head_sha=head_sha,
                overall_status=overall_status,
                apps_to_qa=apps_to_qa,
                results=results,
                in_progress=False,
                failure_summary=failure_summary,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qa_check_write_failed",
                repo=repo,
                head_sha=head_sha,
                error=str(exc),
            )
    else:
        logger.warning(
            "qa_check_skipped_no_head_sha",
            repo=repo,
        )

    # 2. Sticky PR comment
    if issue_number is not None:
        body = render_qa_comment(
            run_number=run_number,
            overall_status=overall_status,
            apps_to_qa=apps_to_qa,
            results=results,
            failure_summary=failure_summary,
            validation_warnings=validation_warnings,
        )
        try:
            await upsert_sticky_comment(
                github_token=github_token,
                repo=repo,
                issue_number=issue_number,
                body=body,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qa_comment_upsert_failed",
                repo=repo,
                issue_number=issue_number,
                error=str(exc),
            )

    return {}
