"""verify node — deterministic verification after an approved review (#298, #299).

Sits between ``review`` (approved) and whatever the route plan says comes
next. No agent runs here and nothing is spent on a model:

1. classify the diff into a verification tier (``verification.tiers``);
2. run the repo's configured test commands on base and head in the dev
   sandbox and compare them (``verification.runner`` / ``.gate``);
3. store the result (``job_verifications`` + ``state["verification"]``);
4. route: new test failures end the job with ``ERR_VERIFY_NEW_TEST_FAILURES``;
   otherwise the route plan continues, with the QA step switched off unless
   the tier needs browser QA AND the repo can boot the app from the branch.

Like every node in this graph it self-routes via ``Command`` exclusively
(RAV-1152). Verification is an optional subsystem: anything that goes wrong
in here degrades to a recorded, non-blocking result and the job carries on.
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command

import embry0.workflows.issue_to_pr.nodes as _nodes
from embry0.execution import events
from embry0.safety.error_codes import ErrorCode
from embry0.workflows.verification.config import VerificationConfig, load_verification_config
from embry0.workflows.verification.gate import GateReport
from embry0.workflows.verification.result import compose_result
from embry0.workflows.verification.runner import run_gate
from embry0.workflows.verification.tiers import classify_diff, plan_browser_qa

logger = structlog.get_logger(__name__)

VERIFICATION_MODES = ("on", "shadow", "off")


def verification_mode(embry0_config: Any) -> str:
    """``VERIFICATION_MODE`` — ``shadow`` (default) | ``on`` | ``off``.

    ``shadow`` records what verification WOULD have decided and changes
    nothing: no blocking, no QA re-routing, nothing written to a forge. An
    unrecognised value, and an absent config, must never stop the
    orchestrator or a job — both read as ``shadow``, the setting that
    cannot fail a job or write anywhere on its own.
    """
    raw = str(getattr(embry0_config, "verification_mode", "shadow") or "shadow").strip().lower()
    if raw in VERIFICATION_MODES:
        return raw
    logger.error("verification_mode_unrecognised", value=raw[:40], treated_as="shadow")
    return "shadow"


def should_verify(state: dict[str, Any], embry0_config: Any) -> bool:
    """Verify only jobs that produced a change: the route plan has a developer
    step (or there is no plan — the legacy developer→review→qa chain)."""
    if verification_mode(embry0_config) == "off":
        return False
    plan = state.get("route_plan") or []
    return not plan or any(step.get("agent_type") == "developer" for step in plan)


def goto_after_review(state: dict[str, Any]) -> Any:
    """The graph target for an approved-or-rejected review, from the route plan.

    Shared by ``review_node`` and ``verify_node`` so both dispatch identically.
    """
    from embry0.workflows.issue_to_pr.route_plan import ROUTE_TARGETS
    from embry0.workflows.issue_to_pr.routing import route_after_review

    target = route_after_review(state)
    if target == "developer":
        return "retry"  # review-fail feedback loop, not a template edge
    if target == "end":
        return END
    return ROUTE_TARGETS.get(target, END)


async def _qa_app_targets(configurable: dict[str, Any], repo: str) -> tuple[list[str], list[str]]:
    """``(managed, deployed)`` app names of the repo's stored QA config.

    No config, or one that does not parse, means no branch-bootable app.
    """
    qa_configs_repo = configurable.get("qa_configs_repo")
    if qa_configs_repo is None or not repo:
        return [], []
    try:
        row = await qa_configs_repo.get(repo)
        if not row or not row.get("yaml_text"):
            return [], []
        from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

        cfg = parse_qa_yaml_v2(row["yaml_text"])
        return cfg.managed_app_names(), cfg.deployed_app_names()
    except Exception:
        logger.warning("verify_qa_config_unreadable", repo=repo, exc_info=True)
        return [], []


async def _stored_config(configurable: dict[str, Any], repo: str) -> tuple[VerificationConfig | None, str | None]:
    prefs_repo = configurable.get("repo_preferences_repo")
    if prefs_repo is None or not repo:
        return None, None
    try:
        row = await prefs_repo.get(repo)
    except Exception:
        logger.warning("verify_preferences_fetch_failed", repo=repo, exc_info=True)
        return None, "the repository's verification settings could not be read"
    config, problem = load_verification_config((row or {}).get("verification"))
    if problem:
        logger.error("verify_config_invalid", repo=repo, problem=problem)
    return config, problem


async def verify_node(state: dict[str, Any], config: RunnableConfig) -> Command[str]:
    writer = _nodes.get_stream_writer()
    writer(events.node_started(node="verify"))

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    mode = verification_mode(configurable.get("embry0_config"))
    job_id = str(state.get("job_id") or "")
    repo = str(state.get("repo") or "")
    docker = configurable.get("docker")
    container_id = state.get("sandbox_container_id")

    from embry0.execution.workspace_diff import compute_changed_files_via_diff, resolve_base_branch
    from embry0.workflows.issue_to_pr.nodes._review import _configured_base_branch

    base_ref = str(state.get("base_branch") or "main")
    changed_files = list(state.get("changed_files") or [])
    if docker is not None and container_id:
        base_ref = await resolve_base_branch(
            docker=docker, container_id=container_id, configured=_configured_base_branch(state)
        )
        if not changed_files:
            try:
                changed_files = await compute_changed_files_via_diff(
                    docker=docker, container_id=container_id, base_branch=base_ref
                )
            except Exception:
                logger.warning("verify_changed_files_diff_failed", job_id=job_id, exc_info=True)

    verification_config, config_problem = await _stored_config(configurable, repo)
    tier = classify_diff(changed_files, verification_config.tiers if verification_config else None)

    gate: GateReport
    if verification_config is None or not verification_config.test_targets:
        gate = GateReport.without_runs(
            "not_configured", note=config_problem or "no test command is configured for this repository"
        )
    elif docker is None or not container_id:
        gate = GateReport.without_runs("error", note="no sandbox was available to run the tests in")
    else:
        writer(events.progress(message="Running the repository's tests on base and head", node="verify"))
        try:
            gate = await run_gate(
                docker=docker,
                container_id=str(container_id),
                config=verification_config,
                changed_files=changed_files,
                base_ref=base_ref,
            )
        except Exception:
            logger.error("verify_gate_crashed", job_id=job_id, exc_info=True)
            gate = GateReport.without_runs("error", note="the test gate stopped unexpectedly")

    qa_block = dict(state.get("qa") or {})
    managed_apps, deployed_apps = await _qa_app_targets(configurable, repo)
    plan = plan_browser_qa(
        tier=tier.tier,
        needs_qa=bool(qa_block.get("needs_qa")),
        managed_apps=managed_apps,
        deployed_apps=deployed_apps,
    )
    result = compose_result(mode=mode, tier=tier, gate=gate, plan=plan, config_error=config_problem)

    logger.info(
        "verification_decided",
        job_id=job_id,
        mode=mode,
        tier=tier.tier,
        gate_verdict=gate.verdict,
        blocking=gate.blocking,
        browser_qa=result["browser_qa"]["planned"],
        browser_qa_reason=plan.reason_code,
        human_merge_only=result["human_merge_only"],
        changed_files=len(changed_files),
    )
    writer(
        events.verification_result(
            tier=tier.tier,
            gate_verdict=gate.verdict,
            blocking=gate.blocking,
            browser_qa=result["browser_qa"],
            human_merge_only=result["human_merge_only"],
            mode=mode,
        )
    )

    verifications_repo = configurable.get("job_verifications_repo")
    if verifications_repo is not None and job_id:
        try:
            await verifications_repo.upsert(job_id=job_id, repo=repo, result=result)
        except Exception:
            logger.error("verification_persist_failed", job_id=job_id, exc_info=True)

    # State (and so every checkpoint) carries the result WITHOUT the runs'
    # output tails; the full document is the job_verifications row.
    lean_gate = {**result["gate"], "runs": [{**run, "output_tail": ""} for run in result["gate"]["runs"]]}
    updates: dict[str, Any] = {"verification": {**result, "gate": lean_gate}, "current_stage": "verify_complete"}
    if changed_files and not state.get("changed_files"):
        updates["changed_files"] = changed_files

    if mode == "shadow":
        # Record only: route exactly as review would have.
        writer(events.node_completed(node="verify", action="shadow"))
        return Command(goto=goto_after_review({**state, **updates}), update=updates)

    if gate.blocking:
        names = sorted({n for t in gate.targets for n in t.new_failures})
        groups = sorted(t.target for t in gate.targets if t.blocking)
        detail = ", ".join(names[:10]) if names else "test group(s) " + ", ".join(groups)
        updates.update(
            {
                "current_stage": "verify_failed",
                "error_code": ErrorCode.VERIFY_NEW_TEST_FAILURES.value,
                # "on this branch" and not "in this job": a pr-followup job is
                # judged on the whole branch's delta against the base, so a
                # failure the earlier commits introduced fails it too.
                "result_summary": (
                    f"Automated tests that pass on {base_ref} fail on this branch: {detail}. "
                    "The whole branch is compared with the base, not only this job's commits."
                ),
            }
        )
        writer(events.node_completed(node="verify", action="failed"))
        return Command(goto=END, update=updates)

    if not plan.run_browser_qa and qa_block.get("needs_qa"):
        # The route plan's qa step is pruned the same way triage prunes it:
        # next_route skips a qa step when needs_qa is false.
        qa_block["needs_qa"] = False
        qa_block["qa_required_reason"] = f"browser QA skipped by verification tiers ({plan.reason_code})"
        updates["qa"] = qa_block
        writer(events.progress(message=f"Browser QA skipped: {plan.reason_code}", node="verify"))

    writer(events.node_completed(node="verify", action="passed"))
    return Command(goto=goto_after_review({**state, **updates}), update=updates)
