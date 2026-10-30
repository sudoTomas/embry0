"""Setting up testing for a repository with the onboarding agent (ADR 0019).

The facts a person sees before starting a setup run, and what happens to
the proposal the run leaves: reading it (ending one whose job stopped
without it), applying it as the test setup, turning it down, or skipping
testing with a starter instead. An apply is refused when the test setup
changed after the run started, when the free check finds a problem every
run would stop on, or when a warning that needs a person was not ticked.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any, Final

import structlog

from embry0.services.bootstrap import render_starter_qa_yaml
from embry0.services.qa_config_check import check_qa_config
from embry0.settings.sections.budget import BUDGET
from embry0.storage.repositories.jobs import TERMINAL_JOB_STATUSES
from embry0.storage.repositories.repo_qa_configs import SetupChanged

logger = structlog.get_logger(__name__)

STALE: Final = "Someone changed the test setup after embry0 made this proposal. Run embry0 again for a fresh one."
ALREADY_RUNNING: Final = "embry0 is already setting up testing for this repository."
HAS_SETUP: Final = "This repository already has a test setup."
NOT_FOUND: Final = "That proposal was not found for this repository."

# Why a proposal that is not waiting for a person cannot be applied or turned down.
_NOT_WAITING: Final[dict[str, str]] = {
    "running": "embry0 is still working on this setup.",
    "applied": "This setup was already applied.",
    "rejected": "This setup was already turned down.",
    "stale": STALE,
    "failed": "embry0 could not finish this setup.",
}

# The typical cost is the median of this many newest completed setup runs.
_COST_RUNS: Final = 20


class Refused(Exception):
    """A setup step said no. ``status_code`` and ``detail`` are the HTTP answer."""

    def __init__(self, status_code: int, detail: str | dict[str, Any]) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def medium_model(app_state: Any) -> str:
    """The deployment's medium tier model, read as GET /config/models reads it."""
    provider_cfg = await app_state.provider_repo.get()
    return str(provider_cfg.get("model_medium") or app_state.config.model_medium)


async def cost_facts(app_state: Any, repo: str) -> dict[str, Any]:
    """What setup runs cost on this embry0, the per-job budget, the model and
    the AI account that would pay. Recorded facts only, never an estimate."""
    db = app_state.db
    recorded_runs = await db.fetchval(
        "SELECT count(*) FROM jobs WHERE pipeline_template = 'onboard' AND status = 'completed'"
    )
    rows = await db.fetch(
        "SELECT total_cost_usd FROM jobs WHERE pipeline_template = 'onboard' AND status = 'completed' "
        "ORDER BY created_at DESC LIMIT $1",
        _COST_RUNS,
    )
    costs = [float(row["total_cost_usd"]) for row in rows]
    policy = getattr(app_state, "budget_policy", None)
    budget = await policy.settings() if policy is not None else (await app_state.settings.snapshot()).get(BUDGET)
    model = await medium_model(app_state)
    return {
        "recorded_runs": int(recorded_runs or 0),
        "typical_cost_usd": statistics.median(costs) if costs else None,
        "per_job_budget_usd": float(budget.max_budget_per_job_usd),
        "model": model,
        "paid_from": await _paid_from(app_state, repo, model),
    }


async def _paid_from(app_state: Any, repo: str, model: str) -> str | None:
    from embry0.agents.providers import provider_for_model

    provider = provider_for_model(model)  # None is a Claude model
    # No usable account is a fact to show, not an error.
    try:
        resolved = await app_state.credential_resolver.resolve_llm(
            repo.lower(), provider.name if provider is not None else "anthropic"
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("setup_cost_no_llm_account", repo=repo, error=str(exc)[:200])
        return None
    return str(resolved.label)


async def start_branch(app_state: Any, repo: str) -> str | None:
    """The branch a setup run reads: the repository's base branch setting, else
    the default branch discovery saw on the forge, else None, which leaves the
    executor to ask the forge (its preference, else the forge's default
    branch, else main; #29) instead of guessing "main" here."""
    prefs = await app_state.repo_preferences_repo.get(repo)
    if prefs and prefs.get("base_branch"):
        return str(prefs["base_branch"])
    fact = await app_state.org_repos_repo.get(repo)
    if fact and fact.get("default_branch"):
        return str(fact["default_branch"])
    return None


async def check_setup(app_state: Any, repo: str, text: str) -> dict[str, Any]:
    """The free check of ``text`` with this repository's settings and Docker grant."""
    env_keys = {str(v["key"]) for v in await app_state.env_repo.get_global()}
    env_keys |= {str(v["key"]) for v in await app_state.env_repo.get_repo(repo)}
    return await check_qa_config(
        text,
        profiles_repo=app_state.profiles_repo,
        env_keys=env_keys,
        docker_access=await app_state.repos_repo.docker_access(repo),
    )


async def settle(app_state: Any, proposal: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The proposal and its job. A ``running`` proposal whose job has ended is
    finished as failed first: onboard runs are not resumed after a restart,
    so nothing else would end it, and it would hold the repository."""
    job_id = proposal.get("job_id")
    job = await app_state.jobs_repo.get(job_id) if job_id else None
    if proposal["status"] == "running" and job is not None and job["status"] in TERMINAL_JOB_STATUSES:
        reason = str(job.get("error_message") or "").strip()
        failure = f"embry0 stopped before it finished: {reason}" if reason else "embry0 stopped before it finished."
        proposals = app_state.repo_setup_proposals_repo
        await proposals.finish(proposal["id"], status="failed", failure=failure)
        proposal = await proposals.get(proposal["id"]) or proposal
    return proposal, job


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def proposal_view(app_state: Any, repo: str) -> dict[str, Any] | None:
    """The repository's latest proposal as the review card reads it, or None."""
    latest = await app_state.repo_setup_proposals_repo.latest(repo)
    if latest is None:
        return None
    proposal, job = await settle(app_state, latest)
    current = await app_state.qa_configs_repo.get(repo)
    proposed = proposal["proposed_yaml"]
    return {
        "id": proposal["id"],
        "repo": proposal["repo"],
        "status": proposal["status"],
        "job_id": proposal["job_id"],
        "job_status": job["status"] if job is not None else None,
        "created_by": proposal["created_by"],
        "created_at": _iso(proposal["created_at"]),
        "decided_by": proposal["decided_by"],
        "decided_at": _iso(proposal["decided_at"]),
        "notes_md": proposal["notes_md"],
        "failure": proposal["failure"],
        "smoke": proposal["smoke"],
        "proposed_yaml": proposed,
        "current_yaml": current["yaml_text"] if current is not None else None,
        "check": await check_setup(app_state, repo, proposed) if proposed is not None else None,
    }


async def _waiting(app_state: Any, repo: str, proposal_id: str) -> dict[str, Any]:
    proposal: dict[str, Any] | None = await app_state.repo_setup_proposals_repo.get(proposal_id)
    if proposal is None or proposal["repo"] != repo:
        raise Refused(404, NOT_FOUND)
    if proposal["status"] != "pending":
        raise Refused(409, _NOT_WAITING[proposal["status"]])
    return proposal


async def _decided_meanwhile(proposals: Any, proposal_id: str) -> Refused:
    """The refusal for a proposal someone else decided after ``_waiting`` read it."""
    now = await proposals.get(proposal_id)
    return Refused(409, _NOT_WAITING[now["status"]] if now is not None else NOT_FOUND)


async def apply_proposal(
    app_state: Any, repo: str, proposal_id: str, *, acknowledged: list[str], actor: str
) -> int | None:
    """Write a waiting proposal as the test setup and return the revision id
    (None when the text equals the stored setup)."""
    proposals = app_state.repo_setup_proposals_repo
    proposal = await _waiting(app_state, repo, proposal_id)
    configs = app_state.qa_configs_repo
    current = await configs.get(repo)
    if (current["updated_at"] if current is not None else None) != proposal["base_updated_at"]:
        await proposals.decide(proposal_id, status="stale", decided_by=actor)
        raise Refused(409, STALE)

    text = str(proposal["proposed_yaml"] or "")
    check = await check_setup(app_state, repo, text)
    if not check["ok"]:
        message = " ".join(f"{p['plain']} {p['fix']}".strip() for p in check["problems"])
        raise Refused(422, {"message": message, "problems": check["problems"]})
    missing = list(
        dict.fromkeys(w["code"] for w in check["warnings"] if w["confirm"] and w["code"] not in acknowledged)
    )
    if missing:
        raise Refused(422, {"message": "Tick each warning before you apply this setup.", "missing": missing})

    # The checks above answer early; this transaction is the one that counts.
    # The decision and the write commit together, and only while the
    # proposal is still pending and the setup is still the one the run
    # started from, so a turn-down or a save that landed meanwhile wins.
    applied = False
    revision_id: int | None = None
    try:
        async with app_state.db.transaction() as conn:
            applied = await proposals.decide(proposal_id, status="applied", decided_by=actor, conn=conn)
            if applied:
                _, revision_id = await configs.upsert_recorded_if_unchanged(
                    conn,
                    repo,
                    text,
                    base_updated_at=proposal["base_updated_at"],
                    updated_by=actor,
                    source="proposal",
                    reason=f"proposal {proposal_id}",
                )
    except SetupChanged:
        await proposals.decide(proposal_id, status="stale", decided_by=actor)
        raise Refused(409, STALE) from None
    except ValueError as exc:
        # As the PUT: a deployment with no default forge will not register the repository.
        raise Refused(422, str(exc)) from exc
    if not applied:
        raise await _decided_meanwhile(proposals, proposal_id)
    return revision_id


async def reject_proposal(app_state: Any, repo: str, proposal_id: str, *, actor: str) -> None:
    proposals = app_state.repo_setup_proposals_repo
    await _waiting(app_state, repo, proposal_id)
    if not await proposals.decide(proposal_id, status="rejected", decided_by=actor):
        raise await _decided_meanwhile(proposals, proposal_id)


async def skip_testing(app_state: Any, repo: str, *, actor: str) -> int | None:
    """Store the starter with testing off (``qa_required: never``), so jobs
    skip the test step instead of failing at it for want of a setup. Makes
    no forge call, so it works for any forge."""
    configs = app_state.qa_configs_repo
    if await configs.get(repo) is not None:
        raise Refused(409, HAS_SETUP)
    revision_id: int | None
    try:
        _, revision_id = await configs.upsert_recorded(
            repo, render_starter_qa_yaml(repo), updated_by=actor, source="person", reason="skip testing"
        )
    except ValueError as exc:
        raise Refused(422, str(exc)) from exc
    return revision_id
